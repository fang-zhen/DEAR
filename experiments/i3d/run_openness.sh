#!/bin/bash

export CUDA_HOME='/usr/local/cuda'

pwd_dir=$pwd
cd ../../

source activate mmaction

OOD_DATA=$1  # HMDB or MiT
case ${OOD_DATA} in
    HMDB)
    NUM_CLASSES=51
    ;;
    MiT)
    NUM_CLASSES=305
    ;;
    *)
    echo "Invalid OOD Dataset: "${OOD_DATA}
    exit
    ;;
esac

# OOD Detection comparison
python experiments/compare_openness.py \
    --base_model i3d \
    --baselines I3D_Dropout_BALD I3D_BNN_BALD I3D_EDLNoKLAvUCDebias_EDL \
    --thresholds 0.000423 0.000024 0.004550 \
    --styles b k r \
    --ood_data ${OOD_DATA} \
    --ood_ncls ${NUM_CLASSES} \
    --ind_ncls 101 \
    --result_png F1_openness_compare_${OOD_DATA}2.png
    

cd $pwd_dir
echo "Experiments finished!"