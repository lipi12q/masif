import tensorflow as tf
import numpy.matlib 
import os
import numpy as np
from IPython.core.debugger import set_trace
from scipy.spatial import cKDTree
from sklearn.metrics import roc_auc_score
from tensorflow import keras
import time
#import pandas as pd
import pickle
import sys

def auroc(y_true, y_pred):
    return tf.py_func(roc_auc_score, (y_true, y_pred), tf.double)

config = tf.ConfigProto()
config.gpu_options.allow_growth = True
session = tf.Session(config=config)

np.random.seed(42)
tf.random.set_random_seed(42)

data_dir = 'transformation_data/'

with open('../lists/training.txt') as f:
    training_list = f.read().splitlines()

n_positives = 100
n_negatives = 100
max_rmsd = 5.0
max_npoints = 200
inlier_distance = 1.0
n_features = 2

data_list = os.listdir(data_dir)
data_list = [os.path.join(data_dir,d) for d in data_list \
            if (os.path.exists(os.path.join(data_dir,d,'target_patch.npy'))) \
            and str(d).split('/')[-1] in training_list]

all_features = np.empty((len(data_list)*(n_positives+n_negatives),max_npoints,n_features))
all_labels = np.empty((len(data_list)*(n_positives+n_negatives),1))
all_scores = np.empty((len(data_list)*(n_positives+n_negatives),1))
all_npoints = []
all_idxs = []
all_nsources = []
n_samples = 0

for i,d in enumerate(data_list):
    
    source_patches = np.load(os.path.join(d,'aligned_source_patches.npy'), allow_pickle=True)
    target_patch = np.load(os.path.join(d,'target_patch.npy'))
    source_descs = np.load(os.path.join(d,'aligned_source_patches_descs.npy'), allow_pickle=True)
    target_desc = np.load(os.path.join(d,'target_patch_descs.npy'), allow_pickle=True)

    # Find correspondences between source and target.
    # Make a target_patch cKDTree
    ckd = cKDTree(target_patch)
    features = np.zeros((len(source_descs), max_npoints, n_features))
    inlier_scores = []
    for iii, source_patch in enumerate(source_patches): 
        dist, corr = ckd.query(source_patch)
        # Compute the descriptor distance. 
        desc_dist = np.sqrt(np.sum(np.square(source_descs[iii] - target_desc[corr]), axis=1))
        features[iii, 0:len(desc_dist), :] = np.vstack([dist, desc_dist]).T
        # Quickly compute an inlier rate.
        inliers = np.sum(dist < 1.5)/float(len(dist))
        inlier_scores.append(inliers)

    source_patch_rmsds = np.load(os.path.join(d,'source_patch_rmsds.npy'), allow_pickle=True)
    assert(len(source_patch_rmsds)== len(source_patches))
    positive_alignments = np.where(source_patch_rmsds<max_rmsd)[0]
    if len(positive_alignments)==0:#<n_positives:
        continue

    if len(positive_alignments) > n_positives:
        chosen_positives = np.random.choice(positive_alignments,n_positives,replace=False)
    else:
        factor = n_positives/len(positive_alignments)
        positive_alignments = np.repeat(positive_alignments, factor+1)
        chosen_positives = positive_alignments[0:n_positives]

    negative_alignments = np.where(source_patch_rmsds>=max_rmsd)[0]
    # Always include include half of the best inliers. 
    negative_alignments_top = np.argsort(inlier_scores)[::-1][:n_negatives//2]
    negative_alignments_top = np.intersect1d(negative_alignments_top, negative_alignments)

    chosen_negatives = np.random.choice(negative_alignments,n_negatives-len(negative_alignments_top),replace=False)
    chosen_alignments = np.concatenate([chosen_positives,negative_alignments_top, chosen_negatives])

    n_sources = len(features)
    features = features[chosen_alignments]
        
    labels = np.expand_dims(np.concatenate([np.ones_like(chosen_positives),np.zeros_like(negative_alignments_top),np.zeros_like(chosen_negatives)]),1)
    
    all_features[n_samples:n_samples+len(chosen_alignments),:,:] = features
    all_labels[n_samples:n_samples+len(chosen_alignments)] = labels
    n_samples += len(chosen_alignments)


all_features = all_features[:n_samples]
all_labels = all_labels[:n_samples]

all_idxs = np.concatenate([(n_positives+n_negatives)*[i] for i in range(int(all_features.shape[0]/(n_positives+n_negatives)))])

reg = keras.regularizers.l2(l=0.0)
model = keras.models.Sequential()
model.add(keras.layers.Conv1D(filters=16,kernel_size=1,strides=1))
model.add(keras.layers.BatchNormalization())
model.add(keras.layers.ReLU())
model.add(keras.layers.Conv1D(filters=32,kernel_size=1,strides=1))
model.add(keras.layers.BatchNormalization())
model.add(keras.layers.ReLU())
model.add(keras.layers.Conv1D(filters=64,kernel_size=1,strides=1))
model.add(keras.layers.BatchNormalization())
model.add(keras.layers.ReLU())
model.add(keras.layers.Conv1D(filters=128,kernel_size=1,strides=1))
model.add(keras.layers.BatchNormalization())
model.add(keras.layers.ReLU())
model.add(keras.layers.Conv1D(filters=256,kernel_size=1,strides=1))
model.add(keras.layers.BatchNormalization())
model.add(keras.layers.ReLU())
model.add(keras.layers.GlobalAveragePooling1D())
model.add(keras.layers.Dense(128,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(64,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(32,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(16,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(8,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(4,activation=tf.nn.relu,kernel_regularizer=reg))
model.add(keras.layers.Dense(2, activation='softmax'))

opt = keras.optimizers.Adam(lr=1e-4)
model.compile(optimizer=opt,loss='sparse_categorical_crossentropy',metrics=['accuracy'])

callbacks = [
    keras.callbacks.ModelCheckpoint(filepath='models/nn_score/{}.hdf5'.format('trained_model'),save_best_only=True,monitor='val_loss',save_weights_only=True),\
    keras.callbacks.TensorBoard(log_dir='./logs/nn_score',write_graph=False,write_images=True)\
]
history = model.fit(all_features,all_labels,batch_size=32,epochs=50,validation_split=0.1,shuffle=True, callbacks=callbacks)
#history = model.fit(all_features,all_labels,batch_size=128,epochs=50,validation_split=0.1,shuffle=True,class_weight={0:1.0/n_negatives,1:1.0/n_positives}, callbacks=callbacks)
