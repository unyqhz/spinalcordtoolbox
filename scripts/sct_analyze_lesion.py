#!/usr/bin/env python

# Analyze lesions
#
# Copyright (c) 2014 Polytechnique Montreal <www.neuro.polymtl.ca>
# Author: Charley
# Modified: 2017-07-26
#
# About the license: see the file LICENSE.TXT

import os
import shutil
import sys
import numpy as np
import itertools
from math import radians, pi, sqrt
from skimage.measure import label, regionprops
import pandas as pd

from sct_maths import binarise
from msct_image import Image
from msct_parser import Parser
from sct_image import set_orientation, get_orientation
from sct_utils import (add_suffix, extract_fname, printv, run,
                       slash_at_the_end, Timer, tmp_create, get_absolute_path)
from sct_straighten_spinalcord import smooth_centerline
from msct_types import Centerline


'''
TODO:
  - Suggestion Sara: Ne mettre qu'un seul message pour SUM!= 100 (avec un OR)
  - PVE
'''


def get_parser():
    # Initialize the parser
    parser = Parser(__file__)
    parser.usage.set_description('Extraction of measures from each lesion. The input is a lesion mask (binary file) identified with value=1 (background=0). The function then assigns an ID value for each lesion (1, 2, 3, etc.) and outputs morphometric measures for each lesion:'
                                    '\n- volume [mm^3]'
                                    '\n- length [mm]: length along the Superior-Inferior axis'
                                    '\n- max_equivalent_diameter [mm]: maximum diameter of the lesion, when approximating the lesion as a circle in the axial cross-sectional plane orthogonal to the spinal cord'
                                    '\nIf an image (e.g. T2w or T1w image, texture image) is provided, it computes the averaged value of this image within each lesion.'
                                    '\nIf a registered template is provided, it computes the proportion of lesion in (1) each vertebral level, (2) GM and WM (3), ... compared to the total lesion load.'
                                    '\nN.B. If the proportion of lesion in each region (e.g., WM and GM) does not sum up to 100%, it means that the registered template does not fully cover the lesion, in that case you might want to check the registration results.')
    parser.add_option(name="-m",
                      type_value="file",
                      description="Lesion mask to analyze",
                      mandatory=True,
                      example='t2_lesion.nii.gz')
    parser.add_option(name="-s",
                      type_value="file",
                      description="Spinal cord centerline or segmentation file, which will be used to correct morphometric measures with cord angle with respect to slice.",
                      mandatory=False,
                      example='t2_seg.nii.gz')
    parser.add_option(name="-i",
                      type_value="file",
                      description="Image from which to extract average values within lesions (e.g. T2w or T1w image, texture image).",
                      mandatory=False,
                      example='t2.nii.gz')
    parser.add_option(name="-f",
                      type_value="str",
                      description="Path to folder containing the atlas/template registered to the anatomical image.",
                      mandatory=False,
                      example="./label")
    parser.add_option(name="-ofolder",
                      type_value="folder_creation",
                      description="Output folder",
                      mandatory=False,
                      example='./')
    parser.add_option(name="-r",
                      type_value="multiple_choice",
                      description="Remove temporary files.",
                      mandatory=False,
                      default_value='1',
                      example=['0', '1'])
    parser.add_option(name="-v",
                      type_value='multiple_choice',
                      description="Verbose: 0 = nothing, 1 = classic, 2 = expended",
                      mandatory=False,
                      example=['0', '1', '2'],
                      default_value='1')

    return parser

class AnalyzeLeion:
  def __init__(self, fname_mask, fname_sc, fname_ref, path_template, path_ofolder, verbose):
    
    self.fname_mask = fname_mask
    self.fname_sc = fname_sc
    self.fname_ref = fname_ref
    self.path_template = path_template
    self.path_ofolder = path_ofolder
    self.verbose = verbose

    # create tmp directory
    self.tmp_dir = tmp_create(verbose=verbose)  # path to tmp directory

    self.fname_label = None

    data_dct = {}
    column_lst = ['label', 'volume [mm3]', 'length [mm]', 'max_equivalent_diameter [mm]']
    if self.fname_ref is not None:
      for feature in ['mean', 'std']:
        column_lst.append(feature+'_'+extract_fname(self.fname_ref)[1])
    for column in column_lst:
      data_dct[column] = None
    self.data_pd = pd.DataFrame(data=data_dct,index=range(0),columns=column_lst)

    self.orientation = None

    self.angles = None

    self.volumes = None

    self.path_levels = self.path_template+'template/PAM50_levels.nii.gz'
    self.path_gm = self.path_template+'template/PAM50_gm.nii.gz'
    self.path_wm = self.path_template+'template/PAM50_wm.nii.gz'
    self.vert_lst = None

    self.excel_name = None
    self.pickle_name = None

  def analyze(self):
    self.ifolder2tmp()

    # Orient input image(s) to RPI
    self.orient2rpi()

    # Binarize the input image if needed
    self.binarize()

    # Label connected regions of the masked image
    self.label_lesion()

    # Compute angle for CSA correction
    self.angle_correction()

    # Compute lesion volume, equivalent diameter, (S-I) length, max axial nominal diameter
    # if registered template provided: across vertebral level, GM, WM, within WM/GM tracts...
    self.measure()

    # Compute mean, median, min, max value in each labeled lesion
    if self.fname_ref is not None:
      self.measure_within_im()

    # reorient data if needed
    self.reorient()

    # print averaged results
    self.show_total_results()

    # # save results in excel and pickle files
    self.pack_measures()

    # save results to ofolder
    self.tmp2ofolder()


  def tmp2ofolder(self):

    os.chdir('..') # go back to original directory

    printv('\nSave results files...', self.verbose, 'normal')
    printv('\n... measures saved in the files:', self.verbose, 'normal')
    printv('\n  - '+self.path_ofolder+self.excel_name, self.verbose, 'normal')
    printv('\n  - '+self.path_ofolder+self.pickle_name, self.verbose, 'normal')

    for file in [self.fname_label, self.excel_name, self.pickle_name]:
      shutil.copy(self.tmp_dir+file, self.path_ofolder+file)

  def pack_measures(self):

    self.excel_name = extract_fname(self.fname_ref)[1]+'_analyzis.xlsx'
    self.data_pd.to_excel(self.excel_name, index=False)

    self.pickle_name = extract_fname(self.fname_ref)[1]+'_analyzis.pkl'
    self.data_pd.columns = [c.split(' ')[0] for c in self.data_pd.columns]
    self.data_pd.to_pickle(self.pickle_name)

  def show_total_results(self):
    print ' '
    print self.data_pd
    
    printv('\n\nAveraged measures...', self.verbose, 'normal')
    printv('  Volume = '+str(round(np.mean(self.data_pd['volume [mm3]']),2))+'+/-'+str(round(np.std(self.data_pd['volume [mm3]']),2))+' mm^3', self.verbose, type='info')
    printv('  (S-I) Length = '+str(round(np.mean(self.data_pd['length [mm]']),2))+'+/-'+str(round(np.std(self.data_pd['length [mm]']),2))+' mm', self.verbose, type='info')
    printv('  Equivalent Diameter = '+str(round(np.mean(self.data_pd['max_equivalent_diameter [mm]']),2))+'+/-'+str(round(np.std(self.data_pd['max_equivalent_diameter [mm]']),2))+' mm', self.verbose, type='info')

    if 'GM [%]' in self.data_pd:
      printv('  Proportion of lesions in WM / GM = '+str(round(np.mean(self.data_pd['WM [%]']),2))+'% / '+str(round(np.mean(self.data_pd['GM [%]']),2))+'%', self.verbose, type='info')


    printv('\nTotal volume = '+str(round(np.sum(self.data_pd['volume [mm3]']),2))+' mm^3', self.verbose, 'info')
    printv('Lesion count = '+str(len(self.data_pd['volume [mm3]'])), self.verbose, 'info')

  def reorient(self):
    if not self.orientation == 'RPI':
      printv('\nOrient output image to initial orientation...', self.verbose, 'normal')

      self._orient(self.label, 'RPI')

  def measure_within_im(self):
    printv('\nCompute reference image features...', self.verbose, 'normal')
    im_label_data, im_ref_data = Image(self.fname_label).data, Image(self.fname_ref).data

    for lesion_label in [l for l in np.unique(im_label_data) if l]:
      im_label_data_cur = im_label_data == lesion_label
      im_label_data_cur[np.where(im_ref_data==0)] = 0 # if the ref object is eroded compared to the labeled object
      mean_cur, std_cur  = np.mean(im_ref_data[np.where(im_label_data_cur)]), np.std(im_ref_data[np.where(im_label_data_cur)])

      label_idx = self.data_pd[self.data_pd.label==lesion_label].index
      self.data_pd.loc[label_idx, 'mean_'+extract_fname(self.fname_ref)[1]] = mean_cur
      self.data_pd.loc[label_idx, 'std_'+extract_fname(self.fname_ref)[1]] = std_cur
      printv('Mean+/-std of lesion #'+str(lesion_label)+' in '+extract_fname(self.fname_ref)[1]+' file: '+str(round(mean_cur,2))+'+/-'+str(round(std_cur,2)), self.verbose, type='info')


  def _measure_tracts(self, im_lesion, im_tract, idx, p_lst, tract_name):

    im_lesion[np.where(im_tract==0)]=0
    vol_cur = np.sum([np.sum(im_lesion[:,:,zz]) * p_lst[0] * p_lst[1] * p_lst[2] for zz in range(im_lesion.shape[2])])

    self.data_pd.loc[idx, tract_name+' [%]'] = vol_cur*100.0/np.sum(self.volumes[:,idx-1])
    printv('  Proportion of lesion #'+str(int(idx[0])+1)+' in '+tract_name+' : '+str(round(self.data_pd.loc[idx, tract_name+' [%]'],2))+' % ('+str(round(vol_cur,2))+' mm^3)', self.verbose, type='info')
  

  def _measure_vert(self, im_lesion, im_vert, p_lst, idx):

    printv('  Proportion of lesion #'+str(int(idx[0])+1)+' in vertebrae... ', self.verbose, type='info')

    sum_vert = 0.0
    for vert_label in self.vert_lst:
      im_vert_cur, im_lesion_cur = np.copy(im_vert), np.copy(im_lesion)
      im_vert_cur[np.where(im_vert!=vert_label)]=0
      im_lesion_cur[np.where(im_vert_cur==0)]=0
      vol_cur = np.sum([np.sum(im_lesion_cur[:,:,zz]) * p_lst[0] * p_lst[1] * p_lst[2] for zz in range(im_lesion.shape[2])])

      vert_name = 'C'+str(int(vert_label)) if vert_label < 8 else 'T'+str(int(vert_label-7))
      self.data_pd.loc[idx, vert_name+' [%]'] = vol_cur*100.0/np.sum(self.volumes[:,idx-1])
      sum_vert += self.data_pd.loc[idx, vert_name+' [%]'].values[0]
      if vol_cur:
        printv('    - '+vert_name+' : '+str(round(self.data_pd.loc[idx, vert_name+' [%]'],2))+' % ('+str(round(vol_cur,2))+' mm^3)', self.verbose, type='info')

    if np.ceil(sum_vert)!=100:
      printv('WARNING: The proportion of lesion in each vertebral levels does not sum up to 100%, it means that the registered template does not fully cover the lesion, in that case you might want to check the registration results.', type='warning')

  def _measure_volume(self, im_data, p_lst, idx):

    for zz in range(im_data.shape[2]):
      self.volumes[zz,idx-1] = np.sum(im_data[:,:,zz]) * p_lst[0] * p_lst[1] * p_lst[2]

    vol_tot_cur = np.sum(self.volumes[:,idx-1])
    self.data_pd.loc[idx, 'volume [mm3]'] = vol_tot_cur
    printv('  Volume : '+str(round(vol_tot_cur,2))+' mm^3', self.verbose, type='info')

  def _measure_length(self, im_data, p_lst, idx):

    length_cur = np.sum([np.cos(self.angles[zz]) * p_lst[2] for zz in list(np.unique(np.where(im_data)[2]))])

    self.data_pd.loc[idx, 'length [mm]'] = length_cur
    printv('  (S-I) length : '+str(round(length_cur,2))+' mm', self.verbose, type='info')

  def _measure_diameter(self, im_data, p_lst, idx):
    
    area_lst = []
    for zz in range(im_data.shape[2]):
      area_lst.append(np.sum(im_data[:,:,zz]) * np.cos(self.angles[zz]) * p_lst[0] * p_lst[1])
    diameter_cur = sqrt(max(area_lst)/(4*pi))
    
    self.data_pd.loc[idx, 'max_equivalent_diameter [mm]'] = diameter_cur
    printv('  Max. equivalent diameter : '+str(round(diameter_cur,2))+' mm', self.verbose, type='info')

  def measure(self):
    im_lesion = Image(self.fname_label)
    im_lesion_data = im_lesion.data
    p_lst = im_lesion.dim[3:6]

    label_lst = [l for l in np.unique(im_lesion_data) if l]

    # if self.path_template is not None:
    #   if os.path.isfile(self.path_levels):
    #     im_vert_data = Image(self.path_levels).data
    #     self.vert_lst = [v for v in np.unique(im_vert_data) if v]
    #   else:
    #     im_vert_data = None
    #     printv('WARNING: the file '+self.path_levels+' does not exist. Please make sure the template was correctly registered and warped (sct_register_to_template or sct_register_multimodal and sct_warp_template)', type='warning')

    #   if os.path.isfile(self.path_gm):
    #     im_gm_data = Image(self.path_gm).data
    #     im_gm_data = im_gm_data > 0.5
    #   else:
    #     im_gm_data = None
    #     printv('WARNING: the file '+self.path_gm+' does not exist. Please make sure the template was correctly registered and warped (sct_register_to_template or sct_register_multimodal and sct_warp_template)', type='warning')
      
    #   if os.path.isfile(self.path_wm):
    #     im_wm_data = Image(self.path_wm).data
    #     im_wm_data = im_wm_data >= 0.5
    #   else:
    #     im_wm_data = None
    #     printv('WARNING: the file '+self.path_wm+' does not exist. Please make sure the template was correctly registered and warped (sct_register_to_template or sct_register_multimodal and sct_warp_template)', type='warning')

    self.volumes = np.zeros((im_lesion.dim[2],len(label_lst)))

    for lesion_label in label_lst:
      im_lesion_data_cur = im_lesion_data == lesion_label
      printv('\nMeasures on lesion #'+str(lesion_label)+'...', self.verbose, 'normal')

      label_idx = self.data_pd[self.data_pd.label==lesion_label].index
      self._measure_volume(im_lesion_data_cur, p_lst, label_idx)
      self._measure_length(im_lesion_data_cur, p_lst, label_idx)
      self._measure_diameter(im_lesion_data_cur, p_lst, label_idx)

      # if im_vert_data is not None:
      #   self._measure_vert(im_lesion_data_cur, im_vert_data, p_lst, label_idx)

      # if im_gm_data is not None:
      #   self._measure_tracts(np.copy(im_lesion_data_cur), im_gm_data, label_idx, p_lst, 'GM')

      # if im_wm_data is not None:
      #   self._measure_tracts(np.copy(im_lesion_data_cur), im_wm_data, label_idx, p_lst, 'WM')

      # # May be fixed with PVE
      # # Suggestion Sara: Ne mettre qu'un seul message: avec un OR
      # if np.ceil(self.data_pd.loc[label_idx, 'GM [%]'].values[0]+self.data_pd.loc[label_idx, 'WM [%]'].values[0]):
      #   printv('WARNING: The proportion of lesion in GM and WM does not sum up to 100%, it means that the registered template does not fully cover the lesion, in that case you might want to check the registration results.', type='warning')


  def _normalize(self, vect):
      norm = np.linalg.norm(vect)
      return vect / norm

  def angle_correction(self):

    if self.fname_sc is not None:
      im_seg = Image(self.fname_sc)
      data_seg = im_seg.data
      X, Y, Z = (data_seg > 0).nonzero()
      min_z_index, max_z_index = min(Z), max(Z)

      # fit centerline, smooth it and return the first derivative (in physical space)
      x_centerline_fit, y_centerline_fit, z_centerline, x_centerline_deriv, y_centerline_deriv, z_centerline_deriv = smooth_centerline(self.fname_sc, algo_fitting='hanning', type_window='hanning', window_length=80, nurbs_pts_number=3000, phys_coordinates=True, verbose=self.verbose, all_slices=False)
      centerline = Centerline(x_centerline_fit, y_centerline_fit, z_centerline, x_centerline_deriv, y_centerline_deriv, z_centerline_deriv)

      # average centerline coordinates over slices of the image
      x_centerline_deriv_rescorr, y_centerline_deriv_rescorr, z_centerline_deriv_rescorr = centerline.average_coordinates_over_slices(im_seg)[3:]

      # compute Z axis of the image, in physical coordinate
      axis_Z = im_seg.get_directions()[2]

      # Empty arrays in which angle for each z slice will be stored
      self.angles = np.zeros(im_seg.dim[2])

      # for iz in xrange(min_z_index, max_z_index + 1):
      for zz in range(im_seg.dim[2]):
        if zz >= min_z_index and zz <= max_z_index:
          # in the case of problematic segmentation (e.g., non continuous segmentation often at the extremities), display a warning but do not crash
          try: # normalize the tangent vector to the centerline (i.e. its derivative)
            tangent_vect = self._normalize(np.array([x_centerline_deriv_rescorr[zz], y_centerline_deriv_rescorr[zz], z_centerline_deriv_rescorr[zz]]))

          except IndexError:
            printv('WARNING: Your segmentation does not seem continuous, which could cause wrong estimations at the problematic slices. Please check it, especially at the extremities.', type='warning')

          # compute the angle between the normal vector of the plane and the vector z
          self.angles[zz] = np.arccos(np.vdot(tangent_vect, axis_Z))

    else:
      self.angles = np.zeros(Image(self.fname_mask).dim[2])

  def label_lesion(self):
    printv('\nLabel connected regions of the masked image...', self.verbose, 'normal')
    im = Image(self.fname_mask)
    im_2save = im.copy()
    im_2save.data = label(im.data, connectivity=2)

    self.fname_label = add_suffix(self.fname_mask, '_label')
    im_2save.setFileName(self.fname_label)
    im_2save.save()

    self.data_pd['label'] = [l for l in np.unique(im_2save.data) if l]
    printv('Lesion count = '+str(len(self.data_pd['label'])), self.verbose, 'info')

  def binarize(self):
    im = Image(self.fname_mask)
    if len(np.unique(im.data))>2: # if the image is not binarized
      printv('\nBinarize lesion file...', self.verbose, 'normal')
      im_2save = im.copy()
      im_2save.data = binarise(im.data)
      im_2save.setFileName(self.fname_mask)
      im_2save.save()

    elif list(np.unique(im.data))==[0]:
      printv('WARNING: Empty masked image', self.verbose, 'warning')

  def _orient(self, fname, orientation):

    im = Image(fname)
    im = set_orientation(im, orientation)
    im.setFileName(fname)
    im.save() 

  def orient2rpi(self):

    self.orientation = get_orientation(Image(self.fname_mask))

    if not self.orientation == 'RPI':
      printv('\nOrient input image(s) to RPI orientation...', self.verbose, 'normal')

      self._orient(self.fname_mask, 'RPI')
      if self.fname_sc is not None:
        self._orient(self.fname_sc, 'RPI')
      if self.fname_ref is not None:
        self._orient(self.fname_ref, 'RPI')

  def ifolder2tmp(self):
    # copy input image
    if self.fname_mask is not None:
      shutil.copy(self.fname_mask, self.tmp_dir)
      self.fname_mask = ''.join(extract_fname(self.fname_mask)[1:])
    else:
      printv('ERROR: No input image', self.verbose, 'error')

    # copy seg image
    if self.fname_sc is not None:
      shutil.copy(self.fname_sc, self.tmp_dir)
      self.fname_sc = ''.join(extract_fname(self.fname_sc)[1:])

    # copy ref image
    if self.fname_ref is not None:
      shutil.copy(self.fname_ref, self.tmp_dir)
      self.fname_ref = ''.join(extract_fname(self.fname_ref)[1:])

    os.chdir(self.tmp_dir) # go to tmp directory

def main(args=None):
  if args is None:
    args = sys.argv[1:]

  # # create param object
  # param = Param()

  # get parser
  parser = get_parser()
  arguments = parser.parse(args)

  # set param arguments ad inputted by user
  fname_mask = arguments["-m"]

  # SC segmentation
  if '-s' in arguments:
    fname_sc = arguments["-s"]
    if not os.path.isfile(fname_sc):
      fname_sc = None
      printv('WARNING: -s input file: "' + arguments['-s'] + '" does not exist.\n', 1, 'warning')
  else:
    fname_sc = None

  # Reference image
  if '-i' in arguments:
    fname_ref = arguments["-i"]
    if not os.path.isfile(fname_sc):
      fname_ref = None
      printv('WARNING: -i input file: "' + arguments['-i'] + '" does not exist.\n', 1, 'warning')
  else:
    fname_ref = None

  # Path to template
  if '-f' in arguments:
    path_template = slash_at_the_end(arguments["-f"], slash=1)
    if not os.path.isdir(path_template) and os.path.exists(path_template):
      path_template = None
      sct.printv("ERROR output directory %s is not a valid directory" % path_template, 1, 'error')
  else:
    path_template = None

   # Output Folder
  if '-ofolder' in arguments:
    path_results = slash_at_the_end(arguments["-ofolder"], slash=1)
    if not os.path.isdir(path_results) and os.path.exists(path_results):
      printv("ERROR output directory %s is not a valid directory" % path_results, 1, 'error')
    if not os.path.exists(path_results):
      os.makedirs(path_results)
  else:
    path_results = './'

  # Remove temp folder
  if '-r' in arguments:
    rm_tmp = bool(int(arguments['-r']))
  else:
    rm_tmp = True

  # Verbosity
  if '-v' in arguments:
    verbose = int(arguments['-v'])
  else:
    verbose = '1'

  # create the Lesion constructor
  lesion_obj = AnalyzeLeion(fname_mask=fname_mask, 
                            fname_sc=fname_sc, 
                            fname_ref=fname_ref, 
                            path_template=path_template, 
                            path_ofolder=path_results,
                            verbose=verbose)
  # run the analyze
  lesion_obj.analyze()

  # remove tmp_dir
  if rm_tmp:
    shutil.rmtree(lesion_obj.tmp_dir)
        
  # printv('\nDone! To view the labeled lesion file (one value per lesion), type:', verbose)
  # if fname_ref is not None:
  #   printv('fslview ' + path_results + fname_mask + ' ' + path_results + lesion_obj.fname_label + ' -l Red-Yellow -t 0.7 & \n', verbose, 'info')
  # else:
  #   printv('fslview ' + path_results + lesion_obj.fname_label + ' -l Red-Yellow -t 0.7 & \n', verbose, 'info')    
    
if __name__ == "__main__":
    main()