﻿#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Tue Jan 29 14:38:26 2019
@author: mzaiss 
"""

import os, sys
import numpy as np
import scipy
import scipy.io
from  scipy import ndimage
import torch
import cv2
import matplotlib.pyplot as plt
from torch import optim
import core.spins
import core.scanner
import core.opt_helper
import core.target_seq_holder
from sys import platform

from core.pulseq_exporter import pulseq_write_GRE
from core.pulseq_exporter import pulseq_write_RARE
from core.pulseq_exporter import pulseq_write_BSSFP
from core.pulseq_exporter import pulseq_write_EPI

use_gpu = 0
gpu_dev = 0
use_gen_adjoint = False
recreate_pulseq_files = False

# NRMSE error function
def e(gt,x):
    return 100*np.linalg.norm((gt-x).ravel())/np.linalg.norm(gt.ravel())
    
# torch to numpy
def tonumpy(x):
    return x.detach().cpu().numpy()

# get magnitude image
def magimg(x):
  return np.sqrt(np.sum(np.abs(x)**2,2))

def magimg_torch(x):
  return torch.sqrt(torch.sum(torch.abs(x)**2,1))

# device setter
def setdevice(x):
    x = x.float()
    if use_gpu:
        x = x.cuda(gpu_dev)    
    return x
    
def stop():
    sys.tracebacklimit = 0
    class ExecutionControl(Exception): pass
    raise ExecutionControl('stopped by user')
    sys.tracebacklimit = 1000
    
if platform == 'linux':
    basepath = '/media/upload3t/CEST_seq/pulseq_zero/sequences'
else:
    basepath = 'K:\CEST_seq\pulseq_zero\sequences'
    
date_str = "seq190529"
experiment_id = "t03_tgtRARE_tskRARE_128_linear_init"
fullpath_seq = os.path.join(basepath, date_str, experiment_id)

fn_alliter_array = "alliter_arr.npy"
alliter_array = np.load(os.path.join(os.path.join(fullpath_seq, fn_alliter_array)), allow_pickle=True)

alliter_array = alliter_array.item()

# define setup
sz = alliter_array['sz']
NRep = sz[1]
T = sz[0] + 4
NSpins = 2**2
NCoils = alliter_array['all_signals'].shape[1]
noise_std = 0*1e0                               # additive Gaussian noise std
NVox = sz[0]*sz[1]
jobtype = "iter"

if use_gen_adjoint:
    scanner = core.scanner.Scanner_fast(sz,NVox,NSpins,NRep,T,NCoils,noise_std,use_gpu+gpu_dev)
else:
    scanner = core.scanner.Scanner(sz,NVox,NSpins,NRep,T,NCoils,noise_std,use_gpu+gpu_dev)
    
scanner.set_adc_mask()

scanner.B1 = setdevice(torch.from_numpy(alliter_array['B1']))
sequence_class = alliter_array['sequence_class']

nmb_iter = alliter_array['all_signals'].shape[0]

all_sim_reco_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_generalized_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_ifft = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_nufft = np.zeros([nmb_iter,sz[0],sz[1],2])

all_sim_kspace = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_kspace = np.zeros([nmb_iter,sz[0],sz[1],2])

all_real_reco_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_generalized_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_ifft = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_nufft = np.zeros([nmb_iter,sz[0],sz[1],2])

for c_iter in range(0, nmb_iter):
    
    print("Processing the iteration {} ...".format(c_iter))
    
    scanner.adc_mask = setdevice(torch.from_numpy(alliter_array['all_adc_masks'][c_iter]))
    scanner.signal = setdevice(torch.from_numpy(alliter_array['all_signals'][c_iter])).unsqueeze(4)
    scanner.reco = setdevice(torch.from_numpy(alliter_array['reco_images'][c_iter]).reshape([NVox,2]))
    scanner.kspace_loc = setdevice(torch.from_numpy(alliter_array['all_kloc'][c_iter]))
    
    flips = setdevice(torch.from_numpy(alliter_array['flips'][c_iter]))
    event_time = setdevice(torch.from_numpy(alliter_array['event_times'][c_iter]))
    grad_moms = setdevice(torch.from_numpy(alliter_array['grad_moms'][c_iter]))
    
    scanner.init_flip_tensor_holder()
    scanner.set_flipXY_tensor(flips)
    
    # rotate ADC according to excitation phase
    scanner.set_ADC_rot_tensor(-flips[0,:,1] + np.pi/2) #GRE/FID specific
    
    TR=torch.sum(event_time[:,1])
    TE=torch.sum(event_time[:11,1])
    
    scanner.init_gradient_tensor_holder()
    scanner.set_gradient_precession_tensor(grad_moms,sequence_class)  # refocusing=False for GRE/FID, adjust for higher echoes
    

    ###############################################################################
    ######### SIMULATION
    
    # simulation adjoint
    scanner.adjoint()
    sim_reco_adjoint = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_sim_reco_adjoint[c_iter] = sim_reco_adjoint
    
    # simulation generalized adjoint
    if use_gen_adjoint:
        scanner.generalized_adjoint()
        sim_reco_generalized_adjoint = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
        all_sim_reco_generalized_adjoint[c_iter] = sim_reco_generalized_adjoint
    
    # simulation IFFT
    scanner.do_ifft_reco()
    sim_reco_ifft = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_sim_reco_ifft[c_iter] = sim_reco_ifft
    
    # simulation NUFFT
    scanner.do_nufft_reco()
    sim_reco_nufft = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_sim_reco_nufft[c_iter] = sim_reco_nufft
    
    coil_idx = 0
    adc_idx = np.where(scanner.adc_mask.cpu().numpy())[0]
    sim_kspace = scanner.signal[coil_idx,adc_idx,:,:2,0]
    sim_kspace = tonumpy(sim_kspace.detach()).reshape([sz[0],sz[1],2])
    all_sim_kspace[c_iter] = sim_kspace
    
    # send to scanner
    iterfile = "iter" + str(c_iter).zfill(6)
    
    if recreate_pulseq_files:
	    fn_pulseq = "iter" + str(c_iter).zfill(6) + ".seq"
	    iflips = alliter_array['flips'][c_iter]
	    ivent = alliter_array['event_times'][c_iter]
	    gmo = alliter_array['grad_moms'][c_iter]

	    seq_params = iflips, ivent, gmo
	    
	    import time
	    today_datestr = time.strftime('%y%m%d')
	    basepath_out = os.path.join(basepath, "seq" + today_datestr)
	    basepath_out = os.path.join(basepath_out, experiment_id)
	    
	    if sequence_class.lower() == "gre":
		pulseq_write_GRE(seq_params, os.path.join(basepath_out, fn_pulseq), plot_seq=False)
	    elif sequence_class.lower() == "rare":
		pulseq_write_RARE(seq_params, os.path.join(basepath_out, fn_pulseq), plot_seq=False)
	    elif sequence_class.lower() == "bssfp":
		pulseq_write_BSSFP(seq_params, os.path.join(basepath_out, fn_pulseq), plot_seq=False)
	    elif sequence_class.lower() == "epi":
		pulseq_write_EPI(seq_params, os.path.join(basepath_out, fn_pulseq), plot_seq=False)          
        
    
    scanner.send_job_to_real_system(experiment_id, basepath_seq_override=fullpath_seq, jobtype=jobtype, iterfile=iterfile)
    scanner.get_signal_from_real_system(experiment_id, basepath_seq_override=fullpath_seq, jobtype=jobtype, iterfile=iterfile)
    
    real_kspace = scanner.signal[coil_idx,adc_idx,:,:2,0]
    real_kspace = tonumpy(real_kspace.detach()).reshape([sz[0],sz[1],2])
    all_real_kspace[c_iter] = real_kspace
    
    # real adjoint
    scanner.adjoint()
    real_reco_adjoint = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_real_reco_adjoint[c_iter] = real_reco_adjoint
    
    # real generalized adjoint
    if use_gen_adjoint:
        scanner.generalized_adjoint()
        real_reco_generalized_adjoint = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
        all_real_reco_generalized_adjoint[c_iter] = real_reco_generalized_adjoint
    
    # real IFFT
    scanner.do_ifft_reco()
    real_reco_ifft = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_real_reco_ifft[c_iter] = real_reco_ifft
    
    # real NUFFT
    scanner.do_nufft_reco()
    real_reco_nufft = tonumpy(scanner.reco.detach()).reshape([sz[0],sz[1],2])
    all_real_reco_nufft[c_iter] = real_reco_nufft
    
    
draw_iter = 305

# Visualize simulated images

plt.subplot(221)
plt.imshow(magimg(all_sim_reco_adjoint[draw_iter]), interpolation='none')
plt.title("sim ADJOINT")
plt.subplot(222)
plt.imshow(magimg(all_sim_reco_generalized_adjoint[draw_iter]), interpolation='none')
plt.title("sim GENERALIZED ADJOINT") 
plt.subplot(223)
plt.imshow(magimg(all_sim_reco_ifft[draw_iter]), interpolation='none')
plt.title("sim IFFT")
plt.subplot(224)
plt.imshow(magimg(all_sim_reco_nufft[draw_iter]), interpolation='none')
plt.title("sim NUFFT") 

plt.ion()
plt.show()

# Visualize kspace

plt.subplot(121)
plt.imshow(magimg(all_sim_kspace[draw_iter]), interpolation='none')
plt.title("sim kspace pwr")
plt.subplot(122)
plt.imshow(magimg(all_real_kspace[draw_iter]), interpolation='none')
plt.title("real kspace pwr") 

plt.ion()
plt.show()

# Visualize measured images

plt.subplot(221)
plt.imshow(magimg(all_real_reco_adjoint[draw_iter]), interpolation='none')
plt.title("real ADJOINT")
plt.subplot(222)
plt.imshow(magimg(all_real_reco_generalized_adjoint[draw_iter]), interpolation='none')
plt.title("real GENERALIZED ADJOINT") 
plt.subplot(223)
plt.imshow(magimg(all_real_reco_ifft[draw_iter]), interpolation='none')
plt.title("real IFFT")
plt.subplot(224)
plt.imshow(magimg(all_real_reco_nufft[draw_iter]), interpolation='none')
plt.title("real NUFFT") 

plt.ion()
plt.show()

## EXPORT LAND

all_sim_reco_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_generalized_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_ifft = np.zeros([nmb_iter,sz[0],sz[1],2])
all_sim_reco_nufft = np.zeros([nmb_iter,sz[0],sz[1],2])

all_sim_kspace = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_kspace = np.zeros([nmb_iter,sz[0],sz[1],2])

all_real_reco_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_generalized_adjoint = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_ifft = np.zeros([nmb_iter,sz[0],sz[1],2])
all_real_reco_nufft = np.zeros([nmb_iter,sz[0],sz[1],2])

allreco_dict = dict()
allreco_dict['all_sim_reco_adjoint'] = all_sim_reco_adjoint
allreco_dict['all_sim_reco_generalized_adjoint'] = all_sim_reco_generalized_adjoint
allreco_dict['all_sim_reco_ifft'] = all_sim_reco_ifft
allreco_dict['all_sim_reco_nufft'] = all_sim_reco_nufft
allreco_dict['all_sim_kspace'] = all_sim_kspace
allreco_dict['all_real_kspace'] = all_real_kspace
allreco_dict['all_real_reco_adjoint'] = all_real_reco_adjoint
allreco_dict['all_real_reco_generalized_adjoint'] = all_real_reco_generalized_adjoint
allreco_dict['all_real_reco_ifft'] = all_real_reco_ifft
allreco_dict['all_real_reco_nufft'] = all_real_reco_nufft


np.save(os.path.join(os.path.join(fullpath_seq, "allreco_dict.npy")), allreco_dict)
        

    