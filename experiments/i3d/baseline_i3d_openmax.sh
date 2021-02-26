#!/bin/bash

pwd_dir=$pwd
cd ../../

source activate mmaction

DEVICE=$1
OOD_DATASET=$2
IND_DATA='data/ucf101/ucf101_val_split_1_videos.txt'

case ${OOD_DATASET} in
  HMDB)
    # run ood detection on hmdb-51 validation set
    OOD_DATA='data/hmdb51/hmdb51_val_split_1_videos.txt'
    ;;
  MiT)
    # run ood detection on hmdb-51 validation set
    OOD_DATA='data/mit/mit_val_list_videos.txt'
    ;;
  *)
    echo "Dataset not supported: "${OOD_DATASET}
    exit
    ;;
esac
RESULT_DIR='experiments/i3d/results'

CUDA_VISIBLE_DEVICES=${DEVICE} python experiments/baseline_openmax.py \
    --config configs/recognition/i3d/inference_i3d_dnn.py \
	--checkpoint work_dirs/i3d/finetune_ucf101_i3d_dnn/latest.pth \
	--cache_mav_dist experiments/i3d/results_baselines/openmax/ucf101_mav_dist \
	--ind_data ${IND_DATA} \
    --ood_data ${OOD_DATA} \
	--result_prefix experiments/i3d/results_baselines/openmax/I3D_OpenMax_${OOD_DATASET}

cd $pwd_dir
echo "Experiments finished!"