import argparse
import os
import os.path as osp
import torch
import mmcv
from mmaction.apis import init_recognizer
from mmcv.parallel import collate, scatter
from operator import itemgetter
from mmaction.datasets.pipelines import Compose
from mmaction.datasets import build_dataloader, build_dataset
from mmcv.parallel import MMDataParallel
import numpy as np
from scipy.special import xlogy
import matplotlib.pyplot as plt
from tqdm import tqdm


def parse_args():
    parser = argparse.ArgumentParser(description='MMAction2 test')
    # model config
    parser.add_argument('--config', help='test config file path')
    parser.add_argument('--checkpoint', help='checkpoint file/url')
    parser.add_argument('--uncertainty', default='BALD', help='the uncertainty estimation method')
    parser.add_argument('--forward_pass', type=int, default=10, help='the number of forward passes')
    # data config
    parser.add_argument('--label_names', help='label file')
    parser.add_argument('--ind_data', help='the split file of in-distribution testing data')
    parser.add_argument('--ood_data', help='the split file of out-of-distribution testing data')
    # env config
    parser.add_argument('--device', type=str, default='cuda:0', help='CPU/CUDA device option')
    parser.add_argument('--result_file', help='result file')
    args = parser.parse_args()
    return args


def inference_recognizer(model, video_path):
    """Inference a video with the detector.

    Args:
        model (nn.Module): The loaded recognizer.
        video_path (str): The video file path/url or the rawframes directory
            path. If ``use_frames`` is set to True, it should be rawframes
            directory path. Otherwise, it should be video file path.
    """
    cfg = model.cfg
    device = next(model.parameters()).device  # model device
    # build the data pipeline
    test_pipeline = cfg.data.test.pipeline
    test_pipeline = Compose(test_pipeline)
    # prepare data (by default, we use videodata)
    start_index = cfg.data.test.get('start_index', 0)
    data = dict(filename=video_path, label=-1, start_index=start_index, modality='RGB')
    data = test_pipeline(data)
    data = collate([data], samples_per_gpu=1)
    if next(model.parameters()).is_cuda:
        # scatter to specified GPU
        data = scatter(data, [device])[0]

    # forward the model
    with torch.no_grad():
        scores = model(return_loss=False, **data)[0]
    return scores


def apply_dropout(m):
    if type(m) == torch.nn.Dropout:
        m.train()

def parse_listfile(list_file):
    assert os.path.exists(list_file), 'split file does not exist! %s'%(list_file)
    videos_path = os.path.join(os.path.dirname(list_file), 'videos')
    assert os.path.exists(videos_path), 'video path does not exist! %s'%(videos_path)
    # parse file list
    filelist, labels = [], []
    with open(list_file, 'r') as f:
        for line in f.readlines():
            videofile = line.strip().split(' ')[0]
            label = int(line.strip().split(' ')[1])
            videofile_full = os.path.join(videos_path, videofile)
            assert os.path.exists(videofile_full), 'video file does not exist! %s'%(videofile_full)
            filelist.append(videofile_full)
            labels.append(label)
    return filelist, labels

def parse_names(mapfile):
    # construct label map
    with open(mapfile, 'r') as f:
        labelnames = [line.strip().split(' ')[0] for line in f]
    return labelnames

def update_seed(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)

def compute_BALD(predictions):
    """Compute the entropy
       scores: (T x C)
    """
    expected_entropy = - np.mean(np.sum(xlogy(predictions, predictions), axis=1), axis=0)  # mean of entropies (across classes), (scalar)
    expected_p = np.mean(predictions, axis=0)  # mean of all forward passes (C,)
    entropy_expected_p = - np.sum(xlogy(expected_p, expected_p), axis=0)  # the entropy of expect_p (across classes)
    BALD_score = entropy_expected_p - expected_entropy
    if not np.isfinite(BALD_score):
        BALD_score = 9999
    return BALD_score


def run_inference_simple(data_list, model, forward_pass, desc=''):
    """Run the inference to get the scores and uncertainty"""
    # prepare testing data
    videofiles, labels = parse_listfile(data_list)
    # run inference
    all_uncertainties, all_results = [], []
    # for i, (videofile, label) in tqdm(enumerate(zip(videofiles, labels)), total=len(videofiles), desc=desc):
    for i, (videofile, label) in enumerate(zip(videofiles, labels)):
        # if i <= 30:
        #     continue
        all_scores = np.zeros((forward_pass, model.cls_head.num_classes), dtype=np.float32)
        for j in range(10):
            # set new random seed
            update_seed(j * 1234)
            # test a single video or rawframes of a single video
            scores = inference_recognizer(model, videofile)  # (101,)
            all_scores[j] = scores
        # compute the uncertainty
        uncertainty = compute_BALD(all_scores)
        all_uncertainties.append(uncertainty)
        all_results.append(all_scores)
    return all_uncertainties, all_results


def run_inference(model, dataset='ucf101', npass=10):
    # switch config for different dataset
    cfg = model.cfg
    if dataset=='ucf101':
        cfg.data.test.ann_file = args.ind_data
        cfg.data.test.data_prefix = os.path.join(os.path.dirname(args.ind_data), 'videos')
    else:
        cfg.data.test.ann_file = args.ood_data
        cfg.data.test.data_prefix = os.path.join(os.path.dirname(args.ood_data), 'videos')

    # build the dataloader
    dataset = build_dataset(cfg.data.test, dict(test_mode=True))
    dataloader_setting = dict(
        videos_per_gpu=cfg.data.get('videos_per_gpu', 1),
        workers_per_gpu=cfg.data.get('workers_per_gpu', 1),
        dist=False,
        shuffle=False,
        pin_memory=False)
    dataloader_setting = dict(dataloader_setting, **cfg.data.get('test_dataloader', {}))
    data_loader = build_dataloader(dataset, **dataloader_setting)

    # run inference
    model = MMDataParallel(model, device_ids=[0])
    all_uncertainties, all_results = [], []
    prog_bar = mmcv.ProgressBar(len(data_loader.dataset))
    for i, data in enumerate(data_loader):
        # if i <= 29:
        #     print(i)
        #     continue
        all_scores = []
        with torch.no_grad():
            for n in range(npass):
                # set new random seed
                update_seed(n * 1234)
                scores = model(return_loss=False, **data)
                all_scores.append(scores)
        all_scores = np.concatenate(all_scores, axis=0)
        # compute the uncertainty
        uncertainty = compute_BALD(all_scores)
        all_uncertainties.append(uncertainty)
        all_results.append(all_scores)

        # use the first key as main key to calculate the batch size
        batch_size = len(next(iter(data.values())))
        for _ in range(batch_size):
            prog_bar.update()
    return all_uncertainties, all_results


def main():

    # build the recognizer from a config file and checkpoint file/url
    model = init_recognizer(
        args.config,
        args.checkpoint,
        device=device,
        use_frames=False)
    cfg = model.cfg
    # use dropout in testing stage
    if cfg.model.cls_head.type == 'I3DHead':
        model.apply(apply_dropout)
    if cfg.model.cls_head.type == 'I3DBNNHead':
        model.test_cfg.npass = 1
    # set cudnn benchmark
    if cfg.get('cudnn_benchmark', False):
        torch.backends.cudnn.benchmark = True
    cfg.data.test.test_mode = True
    cfg.test_pipeline[2].type = 'PyAVDecode'
    
    if not os.path.exists(args.result_file):
        # prepare result path
        result_dir = os.path.dirname(args.result_file)
        if not os.path.exists(result_dir):
            os.makedirs(result_dir)
        # run inference (OOD)
        ood_uncertainties, ood_results = run_inference(model, dataset='hmdb51', npass=args.forward_pass)
        # ood_uncertainties, ood_results = run_inference_simple(args.ood_data, model, args.forward_pass, desc='OOD data')
        # run inference (IND)
        ind_uncertainties, ind_results = run_inference(model, dataset='ucf101', npass=args.forward_pass)
        # ind_uncertainties, ind_results = run_inference_simple(args.ind_data, model, args.forward_pass, desc='IND data')
        # save
        np.savez(args.result_file[:-4], ind_unctt=ind_uncertainties, ood_unctt=ood_uncertainties, ind_score=ind_results, ood_score=ood_results)
    else:
        results = np.load(args.result_file, allow_pickle=True)
        ind_uncertainties = results['ind_unctt']
        ood_uncertainties = results['ood_unctt']
    # visualize
    plt.figure(figsize=(5,4))  # (w, h)
    data_len = min(len(ind_uncertainties), len(ood_uncertainties))
    # data_len = 200 if data_len > 200 else data_len
    data_plot = np.stack((ind_uncertainties[:data_len], ood_uncertainties[:data_len])).transpose()
    plt.hist(data_plot, 50, histtype='bar', color=['blue', 'red'], label=['in-distribution (UCF-101)', 'out-of-distribution (HMDB-51)'])
    # plt.xlim(0, 0.02)
    # plt.xticks(np.arange(0, 0.021, 0.005))
    plt.legend(prop={'size': 10})
    plt.xlabel('BALD Uncertainty')
    plt.ylabel('density')
    plt.tight_layout()
    plt.savefig(os.path.join(os.path.dirname(args.result_file), 'BALD_distribution.png'))

if __name__ == '__main__':

    args = parse_args()
    # assign the desired device.
    device = torch.device(args.device)

    main()
