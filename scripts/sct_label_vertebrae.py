#!/usr/bin/env python
#########################################################################################
#
# Detect vertebral levels from centerline.
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2013 Polytechnique Montreal <www.neuro.polymtl.ca>
# Authors: Eugenie Ullmann, Karun Raju, Tanguy Duval, Julien Cohen-Adad
#
# About the license: see the file LICENSE.TXT
#########################################################################################

# TODO: add user input option (show sagittal slice)
# TODO: make better distance template

# from msct_base_classes import BaseScript
import sys

from os import chdir
import numpy as np
from scipy.signal import argrelextrema, gaussian, find_peaks_cwt
from sct_utils import extract_fname, printv, run, generate_output_file, tmp_create
from msct_parser import Parser
from msct_image import Image
import scipy.optimize as spo


# PARSER
# ==========================================================================================
def get_parser():
    # parser initialisation
    parser = Parser(__file__)
    parser.usage.set_description('''This program automatically detect the spinal cord in a MR image and output a centerline of the spinal cord.''')
    parser.add_option(name="-i",
                      type_value="file",
                      description="input image.",
                      mandatory=True,
                      example="t2.nii.gz")
    parser.add_option(name="-seg",
                      type_value="file",
                      description="Segmentation or centerline of the spinal cord.",
                      mandatory=True,
                      deprecated_by='-s',
                      example="t2_seg.nii.gz")
    parser.add_option(name="-s",
                      type_value="file",
                      description="Segmentation or centerline of the spinal cord.",
                      mandatory=True,
                      example="t2_seg.nii.gz")
    parser.add_option(name="-initz",
                      type_value=[[','], 'int'],
                      description='Initialize labeling by providing slice number (in superior-inferior direction!!) and disc value. Value corresponds to vertebral level above disc (e.g., for C3/C4 disc, value=3). Separate with ","',
                      mandatory=False,
                      example=['125,3'])
    parser.add_option(name="-initcenter",
                      type_value='int',
                      description='Initialize labeling by providing the disc value centered in the rostro-caudal direction. If the spine is curved, then consider the disc that projects onto the cord at the center of the z-FOV',
                      mandatory=False)
    parser.add_option(name='-o',
                      type_value='file_output',
                      description='Output file',
                      mandatory=False,
                      default_value='',
                      example='t2_seg_labeled.nii.gz')
    parser.add_option(name="-ofolder",
                      type_value="folder_creation",
                      description="Output folder.",
                      mandatory=False,
                      default_value='')
    parser.add_option(name="-denoise",
                      type_value="multiple_choice",
                      description="Apply denoising filter to the data. Sometimes denoising is too aggressive, so use with care.",
                      mandatory=False,
                      default_value='0',
                      example=['0', '1'])
    parser.add_option(name="-laplacian",
                      type_value="multiple_choice",
                      description="Apply Laplacian filtering. More accuracy but could mistake disc depending on anatomy.",
                      mandatory=False,
                      default_value='0',
                      example=['0', '1'])
    parser.add_option(name="-r",
                      type_value="multiple_choice",
                      description="Remove temporary files.",
                      mandatory=False,
                      default_value='1',
                      example=['0', '1'])
    parser.add_option(name="-v",
                      type_value="multiple_choice",
                      description="""Verbose. 0: nothing. 1: basic. 2: extended.""",
                      mandatory=False,
                      default_value='1',
                      example=['0', '1', '2'])
    parser.add_option(name="-h",
                      type_value=None,
                      description="display this help",
                      mandatory=False)
    return parser


# MAIN
# ==========================================================================================
def main(args=None):

    # initializations
    initz = ''
    initcenter = ''

    # check user arguments
    if not args:
        args = sys.argv[1:]

    # Get parser info
    parser = get_parser()
    arguments = parser.parse(sys.argv[1:])
    fname_in = arguments["-i"]
    fname_seg = arguments['-s']
    # contrast = arguments['-t']
    if '-o' in arguments:
        file_out = arguments["-o"]
    else:
        file_out = ''
    if '-ofolder' in arguments:
        path_output = arguments['-ofolder']
    else:
        path_output = ''
    if '-initz' in arguments:
        initz = arguments['-initz']
    if '-initcenter' in arguments:
        initcenter = arguments['-initcenter']
    verbose = int(arguments['-v'])
    remove_tmp_files = int(arguments['-r'])
    denoise = int(arguments['-denoise'])
    laplacian = int(arguments['-laplacian'])

    # create temporary folder
    printv('\nCreate temporary folder...', verbose)
    path_tmp = tmp_create(verbose=verbose)

    # Copying input data to tmp folder
    printv('\nCopying input data to tmp folder...', verbose)
    run('sct_convert -i '+fname_in+' -o '+path_tmp+'data.nii')
    run('sct_convert -i '+fname_seg+' -o '+path_tmp+'segmentation.nii.gz')

    # Go go temp folder
    # path_tmp = '/Users/julien/data/biospective/20151013_demo_spinalcordv2.1.b9/200_006_s2_T2/tmp.151013175622/'
    chdir(path_tmp)

    # create label to identify disc
    printv('\nCreate label to identify disc...', verbose)
    if initz:
        create_label_z('segmentation.nii.gz', initz[0], initz[1])  # create label located at z_center
    elif initcenter:
        # find z centered in FOV
        nii = Image('segmentation.nii.gz')
        nii.change_orientation('RPI')  # reorient to RPI
        nx, ny, nz, nt, px, py, pz, pt = nii.dim  # Get dimensions
        z_center = int(round(nz/2))  # get z_center
        create_label_z('segmentation.nii.gz', z_center, initcenter)  # create label located at z_center
    else:
        printv('\nERROR: You need to initialize the disc detection algorithm using one of these two options: -initz, -initcenter\n', 1, 'error')

    # Straighten spinal cord
    printv('\nStraighten spinal cord...', verbose)
    run('sct_straighten_spinalcord -i data.nii -s segmentation.nii.gz -r 0 -qc 0')
    # run('sct_straighten_spinalcord -i data.nii -s segmentation.nii.gz -r 0 -param all_labels=0,bspline_meshsize=3x3x5 -qc 0')  # here using all_labels=0 because of issue #610

    # Apply straightening to segmentation
    # N.B. Output is RPI
    printv('\nApply straightening to segmentation...', verbose)
    run('sct_apply_transfo -i segmentation.nii.gz -d data_straight.nii -w warp_curve2straight.nii.gz -o segmentation_straight.nii.gz -x linear')
    # Threshold segmentation to 0.5
    run('sct_maths -i segmentation_straight.nii.gz -thr 0.5 -o segmentation_straight.nii.gz')

    # Apply straightening to z-label
    printv('\nDilate z-label and apply straightening...', verbose)
    run('sct_apply_transfo -i labelz.nii.gz -d data_straight.nii -w warp_curve2straight.nii.gz -o labelz_straight.nii.gz -x nn')

    # get z value and disk value to initialize labeling
    printv('\nGet z and disc values from straight label...', verbose)
    init_disc = get_z_and_disc_values_from_label('labelz_straight.nii.gz')
    printv('.. '+str(init_disc), verbose)

    # denoise data
    if denoise:
        printv('\nDenoise data...', verbose)
        run('sct_maths -i data_straight.nii -denoise h=0.05 -o data_straight.nii')

    # apply laplacian filtering
    if laplacian:
        printv('\nApply Laplacian filter...', verbose)
        run('sct_maths -i data_straight.nii -laplace 1 -o data_straight.nii')

    # detect vertebral levels on straight spinal cord
    vertebral_detection('data_straight.nii', 'segmentation_straight.nii.gz', init_disc, verbose)

    # un-straighten labelled spinal cord
    printv('\nUn-straighten labeling...', verbose)
    run('sct_apply_transfo -i segmentation_straight_labeled.nii.gz -d segmentation.nii.gz -w warp_straight2curve.nii.gz -o segmentation_labeled.nii.gz -x nn')

    # Clean labeled segmentation
    printv('\nClean labeled segmentation (correct interpolation errors)...', verbose)
    clean_labeled_segmentation('segmentation_labeled.nii.gz', 'segmentation.nii.gz', 'segmentation_labeled.nii.gz')

    # Build file_out
    if file_out == '':
        path_seg, file_seg, ext_seg = extract_fname(fname_seg)
        file_out = file_seg+'_labeled'+ext_seg

    # come back to parent folder
    chdir('..')

    # Generate output files
    printv('\nGenerate output files...', verbose)
    generate_output_file(path_tmp+'segmentation_labeled.nii.gz', path_output+file_out)

    # Remove temporary files
    if remove_tmp_files == 1:
        printv('\nRemove temporary files...', verbose)
        run('rm -rf '+path_tmp)
    
    # to view results
    printv('\nDone! To view results, type:', verbose)
    printv('fslview '+fname_in+' '+path_output+file_out+' -l Random-Rainbow -t 0.5 &\n', verbose, 'info')



# Detect vertebral levels
# ==========================================================================================
def vertebral_detection(fname, fname_seg, init_disc, verbose):

    shift_AP = 17  # shift the centerline towards the spine (in mm).
    size_AP = 4  # window size in AP direction (=y) in mm
    size_RL = 7  # window size in RL direction (=x) in mm
    size_IS = 7  # window size in IS direction (=z) in mm
    searching_window_for_maximum = 5  # size used for finding local maxima
    # gaussian_std_factor = 5  # the larger, the more weighting towards central value. This value is arbitrary-- should adjust based on large dataset
    fig_anat_straight = 1 # handle for figure
    # define mean distance between adjacent discs: C1/C2 -> C2/C3, C2/C3 -> C4/C5, ..., L1/L2 -> L2/L3.
    mean_distance = np.array([18, 16, 17.0000, 16.0000, 15.1667, 15.3333, 15.8333,   18.1667,   18.6667,   18.6667,
    19.8333,   20.6667,   21.6667,   22.3333,   23.8333,   24.1667,   26.0000,   28.6667,   30.5000,   33.5000,
    33.0000,   31.3330])


    if verbose == 2:
        import matplotlib.pyplot as plt
        plt.ion()  # enables interactive mode

    # open anatomical volume
    img = Image(fname)
    data = img.data

    # smooth data
    from scipy.ndimage.filters import gaussian_filter
    data = gaussian_filter(data, [3, 1, 0], output=None, mode="reflect")

    # get dimension
    nx, ny, nz, nt, px, py, pz, pt = img.dim


    #==================================================
    # Compute intensity profile across vertebrae
    #==================================================

    # convert mm to voxel index
    shift_AP = int(round(shift_AP / py))
    size_AP = int(round(size_AP / py))
    size_RL = int(round(size_RL / px))
    size_IS = int(round(size_IS / pz))

    # define z: vector of indices along spine
    z = range(nz)
    # define xc and yc (centered in the field of view)
    xc = int(round(nx/2))  # direction RL
    yc = int(round(ny/2))  # direction AP

    # display stuff
    if verbose == 2:
        import matplotlib.pyplot as plt
        plt.ion()
        plt.matshow(np.mean(data[xc-size_RL:xc+size_RL, :, :], axis=0).transpose(), fignum=fig_anat_straight, cmap=plt.cm.gray, origin='lower')
        plt.title('Anatomical image')
        plt.autoscale(enable=False)  # to prevent autoscale of axis when displaying plot
        plt.figure(fig_anat_straight) , plt.scatter(yc+shift_AP, init_disc[0], c='y', s=50)  # display init disc
        #plt.text(yc+shift_AP+4, init_disc[0], 'init', verticalalignment='center', horizontalalignment='left', color='yellow', fontsize=15), plt.draw()
        plt.close()


    # FIND DISCS
    # ===========================================================================
    printv('\nDetect intervertebral discs...', verbose)
    # assign initial z and disc
    current_z = init_disc[0]
    current_disc = init_disc[1]

    # adjust to pix size
    mean_distance = mean_distance * pz

    # do local adjustment to be at the center of the disc
    printv('.. local adjustment to center disc', verbose)
    current_z = local_adjustment(xc, yc, current_z, current_disc, data, size_RL, shift_AP, size_IS, searching_window_for_maximum, verbose)
    if verbose == 2:
        plt.figure(fig_anat_straight), plt.scatter(yc+shift_AP, current_z, c='g', s=50)
        plt.text(yc+shift_AP+4, current_z, str(current_disc)+'/'+str(current_disc+1), verticalalignment='center', horizontalalignment='left', color='green', fontsize=15)
        # plt.draw()

    # update initial value
    init_disc[0] = current_z

    # get reference pattern
    pattern_ref = data[xc-size_RL:xc+size_RL+1, yc+shift_AP-size_AP:yc+shift_AP+size_AP+1, current_z-size_IS:current_z+size_IS+1]
    pattern_ref1d = pattern_ref.ravel()

    # compute correlation along spine
    correlation_profile = [get_correlation_profile(pattern_ref, pattern_ref1d, z, xc, yc, size_RL, shift_AP, size_AP, size_IS, data) for z in range(0, nz)]
    correlation_profile = np.nan_to_num(correlation_profile)
    # import matplotlib.pyplot as plt
    # plt.plot(correlation_profile)
    # plt.show()

    # adjust current_disc for positions beginning at 0
    current_disc_adj = current_disc - 1

    # adjust template based on dimensions around specified disc
    z_corr_max_approx = np.asarray(find_peaks_cwt(correlation_profile, np.arange(1, 10)))
    if len(z_corr_max_approx) > 0:
        # get position of initialized disc in z_corr_max_approx
        new_current_disc = np.abs(z_corr_max_approx-current_z).argmin()
        # calculate correcting factor
        if (z_corr_max_approx[new_current_disc+1]-z_corr_max_approx[new_current_disc]) > 2:
            correcting_factor_template = (z_corr_max_approx[new_current_disc+1]-z_corr_max_approx[new_current_disc]) / mean_distance[current_disc_adj]
        elif (z_corr_max_approx[new_current_disc]-z_corr_max_approx[new_current_disc-1]) > 2:
            correcting_factor_template = (z_corr_max_approx[new_current_disc]-z_corr_max_approx[new_current_disc-1]) / mean_distance[current_disc_adj-1]
        else:
            correcting_factor_template = 1
        mean_distance = correcting_factor_template * mean_distance

    # calculate distance from initialized disc based on template
    mean_distance_from_init_disc = np.zeros(len(mean_distance)+1)
    for idistance in range(0, len(mean_distance_from_init_disc)):
        sum_distance_pos = 0
        sum_distance_neg = 0
        if idistance < current_disc_adj:
            for i in range(current_disc_adj-1, idistance-1, -1):
                sum_distance_pos = sum_distance_pos + mean_distance[i]
            mean_distance_from_init_disc[len(mean_distance_from_init_disc)-idistance-1] = int(round(sum_distance_pos))
        if idistance > current_disc_adj:
            for i in range(current_disc_adj,idistance):
                sum_distance_neg = sum_distance_neg - mean_distance[i]
                mean_distance_from_init_disc[len(mean_distance_from_init_disc)-idistance-1] = int(round(sum_distance_neg))
        if idistance == current_disc_adj:
            mean_distance_from_init_disc[len(mean_distance_from_init_disc)-idistance-1] = 0

    # adjust mean_distance_from_init_disc based on the image dimensions
    ind_inf = 0
    ind_sup = len(mean_distance_from_init_disc)-current_disc_adj
    for ind_dist in range(0, len(mean_distance_from_init_disc)):
        if ind_dist<len(mean_distance_from_init_disc)-current_disc_adj:
            if mean_distance_from_init_disc[ind_dist]+current_z<=0:
                ind_inf = ind_inf+1
            else:
                ind_inf = ind_inf
        else:
            if mean_distance_from_init_disc[ind_dist]+current_z>nz:
                ind_sup = ind_sup
            else:
                ind_sup = ind_sup+1

    # cut mean_distance_from_init_disc
    mean_distance_from_init_disc_append = mean_distance_from_init_disc[ind_inf:ind_sup]

    # find z where correlation is maximum in specified window
    mean_distance_from_z0 = mean_distance_from_init_disc_append + current_z
    z_corr_max = np.zeros(len(mean_distance_from_z0))
    for ind_dist in range(0, len(mean_distance_from_z0)):
        # set window dimensions
        if ind_dist == 0:
            lowerlim = 0
            upperlim = int(round(0.5*(mean_distance_from_z0[ind_dist+1]-mean_distance_from_z0[ind_dist])+mean_distance_from_z0[ind_dist]))
        elif ind_dist == len(mean_distance_from_z0)-1:
            lowerlim = int(round(0.5*(mean_distance_from_z0[ind_dist]-mean_distance_from_z0[ind_dist-1])+mean_distance_from_z0[ind_dist-1]))
            upperlim = len(correlation_profile)-1
        else:
            lowerlim = int(round(0.5*(mean_distance_from_z0[ind_dist]-mean_distance_from_z0[ind_dist-1])+mean_distance_from_z0[ind_dist-1]))
            upperlim = int(round(0.5*(mean_distance_from_z0[ind_dist+1]-mean_distance_from_z0[ind_dist])+mean_distance_from_z0[ind_dist]))
        if lowerlim < 0:
            lowerlim = 0
        if upperlim > len(correlation_profile)-1:
            upperlim = len(correlation_profile)-1
        # get correlation profile in specified window
        correlation_profile_window = correlation_profile[lowerlim:upperlim]
        # find z where correlation is maximum
        z_corr_max[ind_dist] = np.argmax(correlation_profile_window)+lowerlim

    # initial guess for adjustment in z
    z_adjustment = np.zeros_like(mean_distance_from_init_disc_append)
    # do optimization
    optimization = spo.basinhopping(get_correlation_sum, z_adjustment,
                                    minimizer_kwargs={"args": (z_corr_max, mean_distance_from_init_disc_append, correlation_profile, nz, current_disc_adj)},
                                    stepsize=1)
    z_adjustment = optimization.x.astype(int)
    
    # calculate positions of discs after optimization
    z_disc_real = np.rint(z_adjustment + z_corr_max + 1)
    # correct if guessed too many disks initially
    diff_z_disc_real = np.diff(z_disc_real)
    positions_to_cut = np.where(diff_z_disc_real < 5)
    z_disc_real = np.delete(z_disc_real, positions_to_cut)
    # find position of specified disk in new z_disc_real
    current_disc_in_z_disc_real = np.abs(z_disc_real-current_z).argmin()

    # create list for z
    list_disc_z = np.zeros(len(z_disc_real))
    list_disc_z = list_disc_z + z_disc_real

    # create list for disc
    disc_inf = current_disc - (len(z_disc_real)-1 - current_disc_in_z_disc_real)
    disc_sup = current_disc + current_disc_in_z_disc_real
    list_disc_value = np.linspace(disc_sup, disc_inf, num=disc_sup-disc_inf+1)


    # LABEL SEGMENTATION
    # open segmentation
    seg = Image(fname_seg)
    for iz in range(nz):
        # get index of the disk above iz
        ind_above_iz = np.nonzero((list_disc_z-iz).clip(0))[0]
        if not ind_above_iz.size:
            # if ind_above_iz is empty, attribute value 0
            # vertebral_level = np.min(labeled_peaks)
            vertebral_level = 0
        else:
            # ind_disk_above = np.where(peaks-iz > 0)[0][0]
            ind_disk_above = min(ind_above_iz)
            # assign vertebral level (add one because iz is BELOW the disk)
            vertebral_level = list_disc_value[ind_disk_above] + 1
            # print vertebral_level
        # get voxels in mask
        ind_nonzero = np.nonzero(seg.data[:, :, iz])
        seg.data[ind_nonzero[0], ind_nonzero[1], iz] = vertebral_level
        if verbose == 2:
            plt.figure(fig_anat_straight)
            plt.scatter(int(round(ny/2)), iz, c=vertebral_level, vmin=min(list_disc_value), vmax=max(list_disc_value), cmap='prism', marker='_', s=200)

    # write file
    seg.file_name += '_labeled'
    seg.save()

    # save figure
    if verbose == 2:
        plt.figure(fig_anat_straight), plt.savefig('../fig_anat_straight_with_labels.png')
        plt.close()


# Create label
# ==========================================================================================
def create_label_z(fname_seg, z, value):
    """
    Create a label at coordinates x_center, y_center, z
    :param fname_seg: segmentation
    :param z: int
    :return: fname_label
    """
    fname_label = 'labelz.nii.gz'
    nii = Image(fname_seg)
    orientation_origin = nii.change_orientation('RPI')  # change orientation to RPI
    nx, ny, nz, nt, px, py, pz, pt = nii.dim  # Get dimensions
    # find x and y coordinates of the centerline at z using center of mass
    from scipy.ndimage.measurements import center_of_mass
    x, y = center_of_mass(nii.data[:, :, z])
    x, y = int(round(x)), int(round(y))
    nii.data[:, :, :] = 0
    nii.data[x, y, z] = value
    # dilate label to prevent it from disappearing due to nearestneighbor interpolation
    from sct_maths import dilate
    nii.data = dilate(nii.data, 3)
    nii.setFileName(fname_label)
    nii.change_orientation(orientation_origin)  # put back in original orientation
    nii.save()
    return fname_label


# Get z and label value
# ==========================================================================================
def get_z_and_disc_values_from_label(fname_label):
    """
    Find z-value and label-value based on labeled image
    :param fname_label: image that contains label
    :return: [z_label, value_label] int list
    """
    nii = Image(fname_label)
    # get center of mass of label
    from scipy.ndimage.measurements import center_of_mass
    x_label, y_label, z_label = center_of_mass(nii.data)
    x_label, y_label, z_label = int(round(x_label)), int(round(y_label)), int(round(z_label))
    # get label value
    value_label = int(nii.data[x_label, y_label, z_label])
    return [z_label, value_label]


# Do local adjustment to be at the center of the current disc
# ==========================================================================================
def local_adjustment(xc, yc, current_z, current_disc, data, size_RL, shift_AP, size_IS, searching_window_for_maximum, verbose):
    """
    Do local adjustment to be at the center of the current disc, using cross-correlation of mirrored disc
    :param current_z: init current_z
    :return: adjusted_z: adjusted current_z
    """
    if verbose == 2:
        import matplotlib.pyplot as plt

    size_AP_mirror = 1
    searching_window = range(-9, 13)
    fig_local_adjustment = 4  # fig number
    thr_corr = 0.15  # arbitrary-- should adjust based on large dataset
    gaussian_std_factor = 3  # the larger, the more weighting towards central value. This value is arbitrary-- should adjust based on large dataset

    # Get pattern centered at current_z = init_disc[0]
    pattern = data[xc-size_RL:xc+size_RL+1, yc+shift_AP-size_AP_mirror:yc+shift_AP+size_AP_mirror+1, current_z-size_IS:current_z+size_IS+1]
    # if pattern is missing data (because close to the edge), do not perform correlation and return current_z
    if not pattern.shape == (int(round(size_RL*2+1)), int(round(size_AP_mirror*2+1)), int(round(size_IS*2+1))):
        printv('.... WARNING: Pattern is missing data (because close to the edge). Using initial current_z provided.', verbose)
        return current_z
    pattern1d = pattern.ravel()
    # compute cross-correlation with mirrored pattern
    I_corr = np.zeros((len(searching_window)))
    ind_I = 0
    for iz in searching_window:
        # get pattern shifted
        pattern_shift = data[xc-size_RL:xc+size_RL+1, yc+shift_AP-size_AP_mirror:yc+shift_AP+size_AP_mirror+1, current_z+iz-size_IS:current_z+iz+size_IS+1]
        # if pattern is missing data (because close to the edge), do not perform correlation and return current_z
        if not pattern_shift.shape == (int(round(size_RL*2+1)), int(round(size_AP_mirror*2+1)), int(round(size_IS*2+1))):
            printv('.... WARNING: Pattern is missing data (because close to the edge). Using initial current_z provided.', verbose)
            return current_z
        # make it 1d
        pattern1d_shift = pattern_shift.ravel()
        # mirror it
        pattern1d_shift_mirr = pattern1d_shift[::-1]
        # compute correlation
        I_corr[ind_I] = np.corrcoef(pattern1d_shift_mirr, pattern1d)[0, 1]
        ind_I = ind_I + 1
    # adjust correlation with Gaussian function centered at 'approx_distance_to_next_disc'
    gaussian_window = gaussian(len(searching_window), std=len(searching_window)/gaussian_std_factor)
    I_corr_adj = np.multiply(I_corr, gaussian_window)
    # display
    if verbose == 2:
        plt.figure(fig_local_adjustment), plt.plot(I_corr), plt.plot(I_corr_adj, 'k')
        plt.legend(['I_corr', 'I_corr_adj'])
        plt.title('Correlation of pattern with mirrored pattern.')
    # Find peak within local neighborhood
    ind_peak = argrelextrema(I_corr_adj, np.greater, order=searching_window_for_maximum)[0]
    if len(ind_peak) == 0:
        printv('.... WARNING: No peak found. Using initial current_z provided.', verbose)
        adjusted_z = current_z
    else:
        # keep peak with maximum correlation
        ind_peak = ind_peak[np.argmax(I_corr_adj[ind_peak])]
        printv('.... Peak found: '+str(ind_peak)+' (correlation = '+str(I_corr_adj[ind_peak])+')', verbose)
        # check if correlation is too low
        if I_corr_adj[ind_peak] < thr_corr:
            printv('.... WARNING: Correlation is too low. Using initial current_z provided.', verbose)
            adjusted_z = current_z
        else:
            adjusted_z = int(current_z + round(searching_window[ind_peak]/2)) + 1
            printv('.... Update init_z position to: '+str(adjusted_z), verbose)
    if verbose == 2:
        # display peak
        plt.figure(fig_local_adjustment), plt.plot(ind_peak, I_corr_adj[ind_peak], 'ro')
        # save and close figure
        plt.figure(fig_local_adjustment), plt.savefig('../fig_local_adjustment_disc'+str(current_disc)+'.png'), plt.close()
    return adjusted_z


# Get correlation profile
# ==========================================================================================
def get_correlation_profile(pattern_ref, pattern_ref1d, z, xc, yc, size_RL, shift_AP, size_AP, size_IS, data):
    # get 3d pattern
    pattern = data[xc-size_RL:xc+size_RL+1,
                  yc+shift_AP-size_AP:yc+shift_AP+size_AP+1,
                  z-size_IS:z+size_IS+1]
    # padding for missing data
    padding_size = pattern_ref.shape[2] - pattern.shape[2]
    pattern = np.pad(pattern, ((0, 0), (0, 0), (0, padding_size)), 'constant', constant_values=0)
    # get 1d pattern
    pattern1d = pattern.ravel()
    # return correlation coefficient between 1d pattern and reference pattern
    return np.corrcoef(pattern1d, pattern_ref1d)[0, 1]


# Get correlation value
# ==========================================================================================
def get_correlation_value(z, correlation_profile):
    return correlation_profile[int(z)]


# Sum correlation with constraint
# ==========================================================================================
def get_correlation_sum(z_adjustment, z_corr_max, mean_distance_from_init_disc_append, correlation_profile, nz, current_disc_adj):
    index = (z_corr_max + z_adjustment).astype(int)
    # correction for z at border of image
    for i in range(0, len(index)):
        if index[i] > nz-1:
            index[i] = nz-1
        if index[i] < 0:
            index[i] = 0
    # correlation value to maximize
    correlation_values = correlation_profile[index]
    # adjust current_disc_adj
    current_disc_adj = len(correlation_values) - current_disc_adj - 1
    # constraint value to minimize
    constraint = np.zeros_like(correlation_values)
    for i in range(0, len(constraint)):
        if i == current_disc_adj:
            constraint[i] = 0  # constraint is 0 for specified disc
        else:
            # distance found must be similar to template distances
            if i < len(constraint)-1:
                constraint[i] = abs((index[i+1] - index[i]) - (mean_distance_from_init_disc_append[i+1] - mean_distance_from_init_disc_append[i]))
            else:
                constraint[i] = abs((index[i] - index[i-1]) - (mean_distance_from_init_disc_append[i] - mean_distance_from_init_disc_append[i-1]))
    # function to minimize
    result = -np.sum(correlation_values - np.multiply(1-correlation_values, constraint))
    return result


# Clean labeled segmentation
# ==========================================================================================
def clean_labeled_segmentation(fname_labeled_seg, fname_seg, fname_labeled_seg_new):
    """
    Clean labeled segmentation by:
      (i)  removing voxels in segmentation_labeled that are not in segmentation and
      (ii) adding voxels in segmentation that are not in segmentation_labeled
    :param fname_labeled_seg:
    :param fname_seg:
    :param fname_labeled_seg_new: output
    :return: none
    """
    # remove voxels in segmentation_labeled that are not in segmentation
    #run('sct_maths -i segmentation_labeled.nii.gz -bin -o segmentation_labeled_bin.nii.gz')
    run('sct_maths -i '+fname_labeled_seg+' -mul '+fname_seg+' -o segmentation_labeled_mul.nii.gz')
    # add voxels in segmentation that are not in segmentation_labeled
    run('sct_maths -i '+fname_labeled_seg+' -dilate 2 -o segmentation_labeled_dilate.nii.gz')  # dilate labeled segmentation
    data_label_dilate = Image('segmentation_labeled_dilate.nii.gz').data
    run('sct_maths -i segmentation_labeled_mul.nii.gz -bin -o segmentation_labeled_mul_bin.nii.gz')
    data_label_bin = Image('segmentation_labeled_mul_bin.nii.gz').data
    data_seg = Image(fname_seg).data
    data_diff = data_seg - data_label_bin
    ind_nonzero = np.where(data_diff)
    im_label = Image('segmentation_labeled_mul.nii.gz')
    for i_vox in range(len(ind_nonzero[0])):
        # assign closest label value for this voxel
        ix, iy, iz = ind_nonzero[0][i_vox], ind_nonzero[1][i_vox], ind_nonzero[2][i_vox]
        im_label.data[ix, iy, iz] = data_label_dilate[ix, iy, iz]
    # save new label file (overwrite)
    im_label.setFileName(fname_labeled_seg_new)
    im_label.save()


# START PROGRAM
# ==========================================================================================
if __name__ == "__main__":
    # call main function
    main()