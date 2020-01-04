"""
Created on Tue Jan 29 14:38:26 2019
@author: mzaiss

"""
experiment_id = 'exA05_gradient_echoFFT'
sequence_class = "gre_dream"
experiment_description = """
FID or 1 D imaging / spectroscopy
"""
excercise = """
A05.1. instead of plotting real and imag if the signal, plot the magnitude (absolute value)
A05.2. compare the current signal, with the magnitude signal when line 114 is uncommented. What do you observe?
A05.3. to separate different frequencies, perform a fourier transform of the signal.
A05.4. generate a whole train of gradient echoes after one excitation
A05.5. uncomment FITTING BLOCK, fit signal, what is the recovery rate of the envelope?
"""
#%%
#matplotlib.pyplot.close(fig=None)
#%%
import os, sys
import numpy as np
import scipy
import scipy.io
from  scipy import ndimage
import torch
import cv2
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
from torch import optim
import core.spins
import core.scanner
import core.nnreco
import core.opt_helper
import core.target_seq_holder
import core.FID_normscan
import warnings
import matplotlib.cbook
warnings.filterwarnings("ignore",category=matplotlib.cbook.mplDeprecation)


from importlib import reload
reload(core.scanner)

double_precision = False
do_scanner_query = False

use_gpu = 1
gpu_dev = 0

if sys.platform != 'linux':
    use_gpu = 0
    gpu_dev = 0
print(experiment_id)    
print('use_gpu = ' +str(use_gpu)) 

# NRMSE error function
def e(gt,x):
    return 100*np.linalg.norm((gt-x).ravel())/np.linalg.norm(gt.ravel())
    
# torch to numpy
def tonumpy(x):
    return x.detach().cpu().numpy()

# get magnitude image
def magimg(x):
  return np.sqrt(np.sum(np.abs(x)**2,2))

def phaseimg(x):
    return np.angle(1j*x[:,:,1]+x[:,:,0])

def magimg_torch(x):
  return torch.sqrt(torch.sum(torch.abs(x)**2,1))

def tomag_torch(x):
    return torch.sqrt(torch.sum(torch.abs(x)**2,-1))

# device setter
def setdevice(x):
    if double_precision:
        x = x.double()
    else:
        x = x.float()
    if use_gpu:
        x = x.cuda(gpu_dev)    
    return x 

#############################################################################
## S0: define image and simulation settings::: #####################################
sz = np.array([16,16])                      # image size
extraMeas = 1                               # number of measurmenets/ separate scans
NRep = extraMeas*sz[1]                      # number of total repetitions
NRep = 16                                  # number of total repetitions
szread=128
T = szread + 5 + 2                               # number of events F/R/P
NSpins = 25**2                               # number of spin sims in each voxel
NCoils = 1                                  # number of receive coil elements
noise_std = 0*1e-3                          # additive Gaussian noise std
kill_transverse = False                     #
import time; today_datestr = time.strftime('%y%m%d')
NVox = sz[0]*sz[1]

#############################################################################
## S1: Init spin system and phantom::: #####################################
# initialize scanned object
spins = core.spins.SpinSystem(sz,NVox,NSpins,use_gpu+gpu_dev,double_precision=double_precision)

cutoff = 1e-12
#real_phantom = scipy.io.loadmat('../../data/phantom2D.mat')['phantom_2D']
#real_phantom = scipy.io.loadmat('../../data/numerical_brain_cropped.mat')['cropped_brain']
#real_phantom = np.zeros((128,128,5), dtype=np.float32); real_phantom[64:80,64:80,:2]=1; real_phantom[64:80,64:80,2]=0.1; real_phantom[64:80,64:80,3]=0
   
real_phantom_resized = np.zeros((sz[0],sz[1],5), dtype=np.float32); 
real_phantom_resized[sz[0]-4:sz[0]-3,sz[1]-4:sz[1]-3,:2]=1; real_phantom_resized[sz[0]-4:sz[0]-3,sz[1]-4:sz[1]-3,2]=0.1; real_phantom_resized[sz[0]-4:sz[0]-3,sz[1]-4:sz[1]-3,3]=0
real_phantom_resized[sz[0]-2:sz[0]-1,sz[1]-2:sz[1]-1,:2]=0.25; real_phantom_resized[sz[0]-2:sz[0]-1,sz[1]-2:sz[1]-1,2]=0.1; real_phantom_resized[sz[0]-2:sz[0]-1,sz[1]-2:sz[1]-1,3]=0

real_phantom_resized[:,:,1] *= 1 # Tweak T1
real_phantom_resized[:,:,2] *= 1 # Tweak T2
real_phantom_resized[:,:,3] += 0 # Tweak dB0
real_phantom_resized[:,:,4] *= 1 # Tweak rB1

spins.set_system(real_phantom_resized)

if 0:
    plt.figure("""phantom""")
    param=['PD','T1','T2','dB0','rB1']
    for i in range(5):
        plt.subplot(151+i), plt.title(param[i])
        ax=plt.imshow(real_phantom_resized[:,:,i], interpolation='none')
        fig = plt.gcf()
        fig.colorbar(ax) 
    fig.set_size_inches(18, 3)
    plt.show()
   
#begin nspins with R2* = 1/T2*
R2star = 30.0
omega = np.linspace(0,1,NSpins) - 0.5   # cutoff might bee needed for opt.
omega = np.expand_dims(omega[:],1).repeat(NVox, axis=1)
omega*=0.99 # cutoff large freqs
omega = R2star * np.tan ( np.pi  * omega)
spins.omega = torch.from_numpy(omega.reshape([NSpins,NVox])).float()
spins.omega = setdevice(spins.omega)
## end of S1: Init spin system and phantom ::: #####################################


#############################################################################
## S2: Init scanner system ::: #####################################
scanner = core.scanner.Scanner_fast(sz,NVox,NSpins,NRep,T,NCoils,noise_std,use_gpu+gpu_dev,double_precision=double_precision)

B1plus = torch.zeros((scanner.NCoils,1,scanner.NVox,1,1), dtype=torch.float32)
B1plus[:,0,:,0,0] = torch.from_numpy(real_phantom_resized[:,:,4].reshape([scanner.NCoils, scanner.NVox]))
B1plus[B1plus == 0] = 1    # set b1+ to one, where we dont have phantom measurements
B1plus[:] = 1
scanner.B1plus = setdevice(B1plus)

#############################################################################
## S3: MR sequence definition ::: #####################################
# begin sequence definition
# allow for extra events (pulses, relaxation and spoiling) in the first five and last two events (after last readout event)
adc_mask = torch.from_numpy(np.ones((T,1))).float()
adc_mask[:5]  = 0
adc_mask[-2:] = 0
scanner.set_adc_mask(adc_mask=setdevice(adc_mask))

# RF events: flips and phases
flips = torch.zeros((T,NRep,2), dtype=torch.float32)
flips[3,0,0] = 90*np.pi/180  # GRE/FID specific, GRE preparation part 1 : 90 degree excitation 
flips = setdevice(flips)
scanner.init_flip_tensor_holder()    
scanner.set_flip_tensor_withB1plus(flips)
# rotate ADC according to excitation phase
rfsign = ((flips[3,:,0]) < 0).float()
scanner.set_ADC_rot_tensor(-flips[3,0,1] + np.pi/2 + np.pi*rfsign) #GRE/FID specific

# event timing vector 
event_time = torch.from_numpy(0.08*1e-3*np.ones((scanner.T,scanner.NRep))).float()
event_time[:,0] =  0.08*1e-3
event_time = setdevice(event_time)

# gradient-driver precession
# Cartesian encoding
grad_moms = torch.zeros((T,NRep,2), dtype=torch.float32)
grad_moms[5:-2,0,0] = 0.5
grad_moms[5:-2,1::2,0] = -1 
grad_moms[5:-2,2::2,0] =  1 
#grad_moms[3,1,1] =  0 
#grad_moms[3,2:,1] =  1 
grad_moms = setdevice(grad_moms)

scanner.init_gradient_tensor_holder()
scanner.set_gradient_precession_tensor(grad_moms,sequence_class)  # refocusing=False for GRE/FID, adjust for higher echoes
## end S3: MR sequence definition ::: #####################################


#############################################################################
## S4: MR simulation forward process ::: #####################################
scanner.init_signal()
scanner.forward_fast(spins, event_time)
  
fig=plt.figure("""signals""")
plt.subplot(211)
#ax=plt.plot(tonumpy(scanner.signal[0,:,:,0,0]).transpose().ravel(),label='real')
#plt.plot(tonumpy(scanner.signal[0,:,:,1,0]).transpose().ravel(),label='imag')
plt.plot(np.sqrt(tonumpy(scanner.signal[0,:,:,0,0])**2+tonumpy(scanner.signal[0,:,:,1,0]**2)).transpose().ravel(),label='abs')
plt.title('signal')
plt.legend()
plt.ion()

fig.set_size_inches(64, 7)

#############################################################################
## S5: MR reconstruction of signal ::: #####################################
# general func
def roll(x,n,dim):
    if dim == 0:
        return torch.cat((x[-n:], x[:-n]))
    elif dim == 1:
        return torch.cat((x[:, -n:], x[:, :-n]), dim=1)        
    else:
        class ExecutionControl(Exception): pass
        raise ExecutionControl('roll > 2 dim = FAIL!')
        return 0
    
if 1:
    scanner.adjoint()
    
    spectrum = scanner.signal[0,adc_mask.flatten()!=0,:,:2,0].clone()  # get all ADC signals
    spectrum[:,0]=0     # remove forst repetion (this was the rewinder)
    plt.plot(tonumpy(spectrum[:,:,0]).transpose().ravel())
    major_ticks = np.arange(0, szread*NRep, szread)
    ax=plt.gca(); ax.set_xticks(major_ticks); ax.grid()
    space = torch.zeros_like(spectrum)
    for i in range(0,NRep):
        space[:,i] = torch.ifft(spectrum[:,i,:],1)
    # fftshift
#    space = roll(space,szread//2-1,0)
#    space = roll(space,NRep//2-1,1)
    plt.plot(20*np.sqrt(tonumpy(space[:,:,0]**2+space[:,:,1]**2)).transpose().ravel())
            
else:
    reco = torch.zeros((sz[1]*szread,2), dtype = torch.float32)
    reco = setdevice(reco)
    nrm = np.sqrt(np.prod(sz))
    r = torch.einsum("ijkln,oijnp->klp",[scanner.G_adj, scanner.signal])
    reco = r[:,:2,0] / nrm
    scanner.reco = reco.reshape([szread,NRep,2]).flip([0,1]).permute([1,0,2]).reshape([szread*NRep,2])

# transpose for adjoint
                
recoimg= (tonumpy(scanner.reco.reshape([sz[0],sz[1],2]).flip([0,1]).permute([1,0,2])))
recoimg_mag=magimg(recoimg)
recoimg_phase = phaseimg(recoimg)

plt.subplot(234)
plt.imshow(real_phantom_resized[:,:,0], interpolation='none')
plt.subplot(235)
plt.plot(20*np.sqrt(tonumpy(space[:,:,0]**2+space[:,:,1]**2)),'d-')
plt.subplot(236)
plt.imshow(recoimg_mag, interpolation='none')
   
plt.show()                     
#%% FITTING BLOCK
#tfull=np.cumsum(tonumpy(event_time).transpose().ravel())
#yfull=tonumpy(scanner.signal[0,:,:,0,0]).transpose().ravel()
##yfull=tonumpy(scanner.signal[0,:,:,1,0]).transpose().ravel()
#idx=tonumpy(scanner.signal[0,:,:,0,0]).transpose().argmax(1)
#idx=idx + np.linspace(0,(NRep-1)*len(event_time[:,0]),NRep,dtype=np.int64)
#t=tfull[idx]
#y=yfull[idx]
#def fit_func(t, a, R,c):
#    return a*np.exp(-R*t) + c   
#
#p=scipy.optimize.curve_fit(fit_func,t,y,p0=(np.mean(y), 1,np.min(y)))
#print(p[0][1])
#
#fig=plt.figure("""fit""")
#ax1=plt.subplot(131)
#ax=plt.plot(tfull,yfull,label='fulldata')
#ax=plt.plot(t,y,label='data')
#plt.plot(t,fit_func(t,p[0][0],p[0][1],p[0][2]),label="f={:.2}*exp(-{:.2}*t)+{:.2}".format(p[0][0], p[0][1],p[0][2]))
#plt.title('fit')
#plt.legend()
#plt.ion()
#
#fig.set_size_inches(64, 7)
#plt.show()
#            