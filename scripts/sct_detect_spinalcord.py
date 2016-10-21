#!/usr/bin/env python
#
# This program takes as input an anatomic image and the centerline or segmentation of its spinal cord (that you can get
# using sct_get_centerline.py or sct_segmentation_propagation) and returns the anatomic image where the spinal
# cord was straightened.
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2013 NeuroPoly, Polytechnique Montreal <www.neuro.polymtl.ca>
# Authors: Julien Cohen-Adad, Geoffrey Leveque, Julien Touati
# Modified: 2014-09-01
#
# License: see the LICENSE.TXT
# ======================================================================================================================
# check if needed Python libraries are already installed or not

import os
import time
import sys
from msct_parser import Parser
import sct_utils as sct
import numpy as np
from msct_image import Image
from sortedcontainers import SortedListWithKey
from operator import itemgetter

try:
    import cPickle as pickle
except:
    import pickle

# # import keras necessary classes
# import theano
# theano.config.floatX = 'float32'

# from keras.models import load_model

from sklearn.externals import joblib
from skimage.feature import hog
from sklearn.svm import SVC
from sklearn.base import BaseEstimator
import matplotlib.pyplot as plt
from scipy.sparse.csgraph import shortest_path


def extract_patches_from_image(image_file, patches_coordinates, patch_size=32, slice_of_interest=None, verbose=1):
    result = []
    for k in range(len(patches_coordinates)):
        if slice_of_interest is None:
            ind = [patches_coordinates[k][0], patches_coordinates[k][1], patches_coordinates[k][2]]
        else:
            ind = [patches_coordinates[k][0], patches_coordinates[k][1], slice_of_interest]

        # Transform voxel coordinates to physical coordinates to deal with different resolutions
        # 1. transform ind to physical coordinates
        ind_phys = image_file.transfo_pix2phys([ind])[0]
        # 2. create grid around ind  - , ind_phys[2]
        grid_physical = np.mgrid[ind_phys[0] - patch_size / 2:ind_phys[0] + patch_size / 2, ind_phys[1] - patch_size / 2:ind_phys[1] + patch_size / 2]
        # 3. transform grid to voxel coordinates
        coord_x = grid_physical[0, :, :].ravel()
        coord_y = grid_physical[1, :, :].ravel()
        coord_physical = [[coord_x[i], coord_y[i], ind_phys[2]] for i in range(len(coord_x))]
        grid_voxel = np.array(image_file.transfo_phys2continuouspix(coord_physical))
        np.set_printoptions(threshold=np.inf)
        # 4. interpolate image on the grid, deal with edges
        patch = np.reshape(image_file.get_values(np.array([grid_voxel[:, 0], grid_voxel[:, 1], grid_voxel[:, 2]]), interpolation_mode=1, boundaries_mode='reflect'), (patch_size, patch_size))

        if verbose == 2:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            cax = ax.imshow(patch, cmap='gray')
            cbar = fig.colorbar(cax, ticks=[0, 255])
            plt.show()

        if patch.shape[0] == patch_size and patch.shape[1] == patch_size:
            result.append(np.expand_dims(patch, axis=0))
    if len(result) != 0:
        return np.concatenate(result, axis=0)
    else:
        return None


def display_patches(patches, nb_of_subplots=None, nb_of_figures_to_display=0):

    if nb_of_subplots is None:
        nb_of_subplots = [4, 4]  # [X, Y]

    nb_of_subplots_per_fig = nb_of_subplots[0] * nb_of_subplots[1]

    nb_of_patches = patches.shape[0]
    if nb_of_patches == 0:
        return

    nb_of_figures_to_display_max = int(nb_of_patches / nb_of_subplots_per_fig)
    if nb_of_figures_to_display <= 0 or nb_of_figures_to_display > nb_of_figures_to_display_max:
        if nb_of_figures_to_display_max == 0:
            nb_of_figures_to_display = 1
        elif nb_of_patches % nb_of_figures_to_display_max != 0:
            nb_of_figures_to_display = nb_of_figures_to_display_max + 1

    for i in range(nb_of_figures_to_display):
        fig = plt.figure('Patches #' + str(i * nb_of_subplots_per_fig) + ' to #' + str((i + 1) * nb_of_subplots_per_fig - 1))
        patches_to_display = patches[i*nb_of_subplots_per_fig:(i+1)*nb_of_subplots_per_fig, :, :]

        for j in range(patches_to_display.shape[0]):
            ax = plt.subplot(nb_of_subplots[0], nb_of_subplots[1], j+1)
            ax.imshow(patches_to_display[j, :, :], vmin=0, vmax=255, cmap='gray')
            ax.set_axis_off()
            ax.set_aspect('equal')

        plt.subplots_adjust(left=0.1, right=0.9, top=0.9, bottom=0.1)
        plt.show()

class Classifier_svm(BaseEstimator):
    def __init__(self, params={}):

        self.clf = SVC()
        self.params = params
 
    def train(self, X, y):
        self.clf.fit(X, y)
 
    def predict(self, X):
        return self.clf.predict_proba(X)

    def save(self, fname_out):
        joblib.dump(self.clf, fname_out + '.pkl')

    def load(self, fname_in):
        clf = joblib.load(fname_in + '.pkl')

        self.clf = clf

        self.params = clf.get_params()
        print self.params

    def set_params(self, params):
        self.clf.set_params(**params)
        self.params = params

def extract_hog_feature(patch_list, param=None):

    if param is None:
        param = {'orientations': 8, 'pixels_per_cell': [6, 6], 'cells_per_block': [3,3],
                'visualize': False, 'transform_sqrt': True}

    X_test = []
    for patch in patch_list:
        hog_feature = np.array(hog(image = patch, orientations=param['orientations'],
                pixels_per_cell=param['pixels_per_cell'], cells_per_block=param['cells_per_block'],
                transform_sqrt=param['transform_sqrt'], visualise=param['visualize']))
        X_test.append(hog_feature)

    X_test = np.array(X_test)

    return X_test

def plot_2D_detections(im_data, slice_number, initial_coordinates, classes_predictions):

    slice_cur = im_data.data[:,:,slice_number]

    fig1 = plt.figure()
    ax1 = plt.subplot(1, 1, 1)
    ax1.imshow(slice_cur,cmap=plt.get_cmap('gray'))

    for coord_mesh in range(len(initial_coordinates)):
        plt.scatter(initial_coordinates[coord_mesh][1], initial_coordinates[coord_mesh][0], c='Red')

    for coord in classes_predictions:
        plt.scatter(initial_coordinates[coord][1], initial_coordinates[coord][0], c='Green')
    
    ax1.set_axis_off()
    plt.show()

def predict(fname_input, fname_model, model, initial_resolution, list_offset, threshold, feature_fct, path_output, verbose=0):
    
    time_prediction = time.time()

    patch_size = 32

    print '\nLoading model...'
    model.load(fname_model)
    print '...\n'

    im_data = Image(fname_input)

    # intensity normalization
    im_data.data = 255.0 * (im_data.data - np.percentile(im_data.data, 0)) / np.abs(np.percentile(im_data.data, 0) - np.percentile(im_data.data, 100))

    nx, ny, nz, nt, px, py, pz, pt = im_data.dim
    print 'Image Dimensions: ' + str([nx, ny, nz]) + '\n'
    nb_voxels_image = nx * ny * nz

    # first round of patch prediction
    initial_coordinates_x = range(0, nx, initial_resolution[0])
    initial_coordinates_y = range(0, ny, initial_resolution[1])
    X, Y = np.meshgrid(initial_coordinates_x, initial_coordinates_y)
    X, Y = X.ravel(), Y.ravel()
    initial_coordinates = [[X[i], Y[i]] for i in range(len(X))]

    nb_voxels_explored = 0
    coord_positive = SortedListWithKey(key=itemgetter(0, 1, 2))
    coord_positive_saved = []

    print 'Initial prediction:\n'
    tot_pos_pred = 0
    nb_slice_no_detection = 0
    for slice_number in range(0, nz, initial_resolution[2]):
        print '... slice #' + str(slice_number) + '/' + str(nz)
        patches = extract_patches_from_image(im_data, initial_coordinates, patch_size=patch_size, slice_of_interest=slice_number, verbose=0)
        patches = np.asarray(patches, dtype=int)
        patches = patches.reshape(patches.shape[0], patches.shape[1], patches.shape[2])

        X_test = feature_fct(patches)
        y_pred = model.predict(X_test)

        y_pred = np.array(y_pred)
        classes_predictions = np.where(y_pred[:, 1] > threshold)[0].tolist()
        print '... # of pos prediction: ' + str(len(classes_predictions)) + '\n'
        tot_pos_pred += len(classes_predictions)
        if not len(classes_predictions):
            nb_slice_no_detection += 1

        if slice_number % 100 == 0 and verbose > 0:
            plot_2D_detections(im_data, slice_number, initial_coordinates, classes_predictions)
        
        coord_positive.update([[initial_coordinates[coord][0], initial_coordinates[coord][1], slice_number] for coord in classes_predictions])
        coord_positive_saved.extend([[initial_coordinates[coord][0], initial_coordinates[coord][1], slice_number, y_pred[coord, 1]] for coord in classes_predictions])
        
        nb_voxels_explored += len(patches)
    
    print '\n# of voxels explored = ' + str(nb_voxels_explored) + '/' + str(nb_voxels_image) + ' (' + str(round(100.0 * nb_voxels_explored / nb_voxels_image, 2)) + '%)'
    print '# of slice without any pos predicted voxel: ' + str(nb_slice_no_detection) + '/' + str(int(float(nz)/initial_resolution[2])) + ' (' + str(round(float(nb_slice_no_detection*initial_resolution[2]*100)/nz,2)) + '%)\n'
    last_coord_positive = coord_positive


    # charley: Tester set_params(class_weight=None)


    # informative data
    iteration = 1

    print '\nIterative prediction based on mathematical morphology'
    while len(last_coord_positive) != 0:
        print '\nIteration #' + str(iteration) + '. # of positive voxel to explore:' + str(len(last_coord_positive))
        current_coordinates = SortedListWithKey(key=itemgetter(0, 1, 2))
        for coord in last_coord_positive:
            for offset in list_offset:
                new_coord = [coord[0] + offset[0], coord[1] + offset[1], coord[2] + offset[2]]
                if 0 <= new_coord[0] < im_data.data.shape[0] and 0 <= new_coord[1] < im_data.data.shape[1] and 0 <= new_coord[2] < im_data.data.shape[2]:
                    if current_coordinates.count(new_coord) == 0:
                        if coord_positive.count(new_coord) == 0:
                            current_coordinates.add(new_coord)

        print 'Patch extraction (N=' + str(len(current_coordinates)) + ')'

        patches = extract_patches_from_image(im_data, current_coordinates, patch_size=patch_size, verbose=0)
        
        if patches is not None:
            patches = np.asarray(patches, dtype=int)
            patches = patches.reshape(patches.shape[0], patches.shape[1], patches.shape[2])

            X_test = feature_fct(patches)
            y_pred = model.predict(X_test)

            y_pred = np.array(y_pred)
            classes_predictions = np.where(y_pred[:, 1] > threshold)[0].tolist()

            if iteration % 5 == 0 and verbose > 1:
                patches_positive = np.squeeze(patches[np.where(y_pred[:, 1] > threshold)[0]])
                display_patches(patches_positive, nb_of_subplots=[10, 10], nb_of_figures_to_display=1)

            last_coord_positive = [[current_coordinates[coord][0], current_coordinates[coord][1], current_coordinates[coord][2]] for coord in classes_predictions]
            coord_positive.update(last_coord_positive)
            coord_positive_saved.extend([[current_coordinates[coord][0], current_coordinates[coord][1], current_coordinates[coord][2], y_pred[coord, 1]] for coord in classes_predictions])
            nb_voxels_explored += len(patches)
        else:
            last_coord_positive = []

        iteration += 1

    print '\nTime to predict cord location: ' + str(np.round(time.time() - time_prediction)) + ' seconds'
    print '# of voxels explored = ' + str(nb_voxels_explored) + '/' + str(nb_voxels_image) + ' (' + str(round(100.0 * nb_voxels_explored / nb_voxels_image, 2)) + '%)'
    z_pos_pred = []
    for coord in coord_positive_saved:
        z_pos_pred.append(coord[2])
    z_pos_pred = np.array(z_pos_pred)
    nb_pos_slice = len(np.unique(z_pos_pred))
    print '# of slice without any pos predicted voxel: ' + str(nz-nb_pos_slice) + '/' + str(nz) + ' (' + str(round(100.0 * (nz-nb_pos_slice) / nx, 2)) + '%)\n'



    # write results
    print '\nWriting results'
    input_image = im_data.copy()
    input_image.data *= 0
    for coord in coord_positive_saved:
        input_image.data[coord[0], coord[1], coord[2]] = coord[3]
    path_input, file_input, ext_input = sct.extract_fname(fname_input)
    fname_output = path_output + file_input + "_cord_prediction" + ext_input
    input_image.setFileName(fname_output)
    input_image.save()

    print '\nInput Image: ' + fname_input
    print 'Output Image: ' + fname_output + '\n'





def create_image_graph(fname_seg, path_output):

    # TODO:     Nettoyer code
            #   Initialiser autrement le premier src: centre de masse
            #   Comment faire le lien dans les trous: prediction circulaire via la seed jusqua ce quon trouve

    im = Image(fname_seg)
    im_data = im.data

    image_graph = {}
    cmpt = 0
    for z in range(im.dim[2]):
        coord_0_list = np.where(im_data[:,:,z] > 0)[0].tolist()
        coord_1_list = np.where(im_data[:,:,z] > 0)[1].tolist()
        coord_zip = zip(coord_0_list, coord_1_list)
        prob_list = [im_data[x_i, y_i, z] for x_i, y_i in coord_zip]

        image_graph[z] = {'coords': coord_zip, 'prob': prob_list}

    last_bound = -1
    graph_bound = []
    cmpt = 0
    for z in range(im.dim[2]):
        if len(image_graph[z]['coords']) == 0:
            if last_bound != z-1:
                graph_bound.append(range(last_bound+1,z))
            last_bound = z
            cmpt += 1
    if last_bound != im.dim[2]:
        graph_bound.append(range(last_bound+1,im.dim[2]))

    from scipy.spatial import distance

    node_list = []
    cmpt_bloc = 0
    centerline_coord = {}
    for bloc in graph_bound:

        print '\n Bloc #: ' + str(cmpt_bloc)
        cmpt_bloc += 1

        bloc_matrix = []
        for i in bloc[:-1]:
            bloc_matrix.append(distance.cdist(image_graph[i]['coords'], image_graph[i+1]['coords'], 'euclidean'))

        len_bloc = [len(image_graph[z]['prob']) for z in bloc]
        tot_candidates_bloc = sum(len_bloc)

        matrix_list = []
        lenght_done = 0
        for i in range(len(bloc[:-1])):
            matrix_i = np.ones((len_bloc[i], tot_candidates_bloc))*1000.0
            matrix_i[:,lenght_done + len_bloc[i] : lenght_done + len_bloc[i] + len_bloc[i+1]] = bloc_matrix[i]
            lenght_done += len_bloc[i]
            matrix_list.append(matrix_i)

        matrix_list.append(np.ones((len_bloc[len(bloc)-1], tot_candidates_bloc))*1000.0)

        matrix = np.concatenate(matrix_list)
        print matrix.shape
        matrix[range(matrix.shape[0]), range(matrix.shape[1])] = 0

        matrix_dijkstra = shortest_path(matrix)
        first_slice_info = image_graph[bloc[0]]['prob']
        src = first_slice_info.index(max(first_slice_info))
        
        node_list_cur = []
        node_list_cur.append(src)
        lenght_done = 0
        for i in range(len(bloc[:-1])):
            test = matrix_dijkstra[lenght_done + src,lenght_done + len_bloc[i] : lenght_done + len_bloc[i] + len_bloc[i+1]]

            src = test.argmin()
            node_list_cur.append(src)
            lenght_done += len_bloc[i]
        node_list.append(node_list_cur)

        cmpt = 0
        for i in bloc:
            centerline_coord[i] = [image_graph[i]['coords'][node_list_cur[cmpt]][0], image_graph[i]['coords'][node_list_cur[cmpt]][1], i]
            cmpt += 1
    
    print centerline_coord

    # write results
    print '\nWriting results'
    input_image = im.copy()
    input_image.data *= 0
    for z in range(len(centerline_coord)):
        if len(image_graph[z]['coords']) != 0:
            input_image.data[centerline_coord[z][0], centerline_coord[z][1], z] = 1
    path_input, file_input, ext_input = sct.extract_fname(fname_input)
    fname_output = path_output + file_input + "_cord_prediction_reg" + ext_input
    input_image.setFileName(fname_output)
    input_image.save()

    print '\nInput Image: ' + fname_seg
    print 'Output Image: ' + fname_output + '\n'

        # any(e[1] == z for e in data)
        # if len(image_graph[z]['coords']) != 0:
        #     centerline_coord[z] = []


    # ii, jj = np.unravel_index(test.argmin(), test.shape)
    # coord_0_list = np.where(test == test[ii,jj])[0].tolist()
    # coord_1_list = np.where(test == test[ii,jj])[1].tolist()
    # coord_zip = zip(coord_0_list, coord_1_list)

    # prob_candidates = []
    # for i_0, i_1 in coord_zip:
    #     prob_candidates.append(image_graph[0]['prob'][i_0] + image_graph[0]['prob'][i_1])

    # best_idx_0_1 = coord_zip[prob_candidates.index(max(prob_candidates))]

    # print test[:5,:5]
    # test = matrix_0[:len_bloc_0[0],len_bloc_0[0]:len_bloc_0[0]+len_bloc_0[0+1]]
    # print test[:5,:5]

    # matrix_0_1 = np.hstack((np.ones((len_bloc_0[0], len_bloc_0[1]))*1000.0,
    #                         bloc_matrix_0[0],

    #                         )
#     for graph_node_src in image_graph[0]:
#         for graph_node_dest in image_graph[1]:




def get_parser():
    # Initialize parser
    parser = Parser(__file__)

    # Mandatory arguments
    parser.usage.set_description("This program takes as input an anatomic image and outputs a binary image with the spinal cord centerline/segmentation.")
    parser.add_option(name="-i",
                      type_value="image_nifti",
                      description="input image.",
                      mandatory=True,
                      example="t2.nii.gz")

    parser.add_option(name="-c",
                      type_value="multiple_choice",
                      description="type of image contrast, t2: cord dark / CSF bright ; t1: cord bright / CSF dark.",
                      mandatory=False,
                      example=['t1', 't2'])

    parser.add_option(name="-ofolder",
                      type_value="folder",
                      description="Path output",
                      mandatory=True,
                      default_value='',
                      example="'/Users/chgroc/data/centerline_detection/results3D/")

    parser.add_option(name="-r",
                      type_value="multiple_choice",
                      description="remove temporary files.",
                      mandatory=False,
                      example=['0', '1'],
                      default_value='1')

    parser.add_option(name="-v",
                      type_value="multiple_choice",
                      description="Verbose. 0: nothing, 1: basic, 2: extended.",
                      mandatory=False,
                      example=['0', '1', '2'],
                      default_value='1')

    parser.add_option(name='-qc',
                      type_value='multiple_choice',
                      description='Output images for quality control.',
                      mandatory=False,
                      example=['0', '1'],
                      default_value='0')

    return parser


if __name__ == "__main__":
    parser = get_parser()
    arguments = parser.parse(sys.argv[1:])

    fname_model = '/Users/chgroc/data/spine_detection/model_0-001_0-5_recall/LinearSVM_train'
    model_svm = Classifier_svm()

    # Find threshold value from training
    fname_trial = '/Users/chgroc/data/spine_detection/results_0-001_0-5_recall/LinearSVM_trials.pkl'
    with open(fname_trial) as outfile:    
        trial = pickle.load(outfile)
        outfile.close()
    loss_list = [trial[i]['result']['loss'] for i in range(len(trial))]
    thrsh_list = [trial[i]['result']['thrsh'] for i in range(len(trial))]
    idx_best_params = loss_list.index(min(loss_list))
    threshold = trial[idx_best_params]['result']['thrsh']

    feature_fct = extract_hog_feature

    initial_resolution = [3, 3, 5]
    list_offset = [[xv, yv, zv] for xv in range(-1,1) for yv in range(-1,1) for zv in range(-40,40) if [xv, yv, zv] != [0, 0, 0]]

    fname_input = arguments['-i']

    folder_output = sct.slash_at_the_end(arguments['-ofolder'], slash=1)

    # predict(fname_input, fname_model, model_svm, initial_resolution, list_offset, 
    #             threshold = threshold, feature_fct = feature_fct, path_output = folder_output, verbose=0)
    create_image_graph('/Users/chgroc/data/spine_detection/results3D/ALT_t2_cord_prediction.nii.gz', path_output = folder_output)
    print fname_input