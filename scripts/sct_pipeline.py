#!/usr/bin/env python
"""
#########################################################################################
#
# This function allows to run a function on a large dataset with a set of parameters.
# Results are extracted and saved in a way that they can easily be compared with another set.
#
# Data should be organized as the following:
# (names of images can be changed but must be passed as parameters to this function)
#
# data/
# ......subject_name_01/
# ......subject_name_02/
# .................t1/
# .........................subject_02_anything_t1.nii.gz
# .........................some_landmarks_of_vertebral_levels.nii.gz
# .........................subject_02_manual_segmentation_t1.nii.gz
# .................t2/
# .........................subject_02_anything_t2.nii.gz
# .........................some_landmarks_of_vertebral_levels.nii.gz
# .........................subject_02_manual_segmentation_t2.nii.gz
# .................t2star/
# .........................subject_02_anything_t2star.nii.gz
# .........................subject_02_manual_segmentation_t2star.nii.gz
# ......subject_name_03/
#          .
#          .
#          .
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2015 Polytechnique Montreal <www.neuro.polymtl.ca>
# Author: Sara Dupont, Benjamin De Leener
# Modified: 2015-09-30
#
# About the license: see the file LICENSE.TXT
#########################################################################################
usage:

    sct_pipeline  -f sct_a_tool -d /path/to/data/  -p  \" sct_a_tool option \" -cpu-nb 8
"""

# TODO: read_database: hard coded fields to put somewhere else (e.g. config file)

import commands
import copy_reg
# import json
import os
import platform
import signal
import sys
import types
import copy
from time import time, strftime
if "SCT_MPI_MODE" in os.environ:
    from distribute2mpi import MpiPool as Pool
else:
    from multiprocessing import Pool
import pandas as pd
import sct_utils as sct
import msct_parser
import glob

# get path of the toolbox
# TODO: put it back below when working again (julien 2016-04-04)
# <<<
# OLD
# status, path_sct = commands.getstatusoutput('echo $SCT_DIR')
# NEW
path_script = os.path.dirname(__file__)
path_sct = os.path.dirname(path_script)
# >>>
# append path that contains scripts, to be able to load modules
sys.path.append(path_sct + '/scripts')
sys.path.append(path_sct + '/testing')


def _pickle_method(method):
    """
    Author: Steven Bethard (author of argparse)
    http://bytes.com/topic/python/answers/552476-why-cant-you-pickle-instancemethods
    """
    func_name = method.im_func.__name__
    obj = method.im_self
    cls = method.im_class
    cls_name = ''
    if func_name.startswith('__') and not func_name.endswith('__'):
        cls_name = cls.__name__.lstrip('_')
    if cls_name:
        func_name = '_' + cls_name + func_name
    return _unpickle_method, (func_name, obj, cls)


def _unpickle_method(func_name, obj, cls):
    """
    Author: Steven Bethard
    http://bytes.com/topic/python/answers/552476-why-cant-you-pickle-instancemethods
    """
    for cls in cls.mro():
        try:
            func = cls.__dict__[func_name]
        except KeyError:
            pass
        else:
            break
    return func.__get__(obj, cls)

copy_reg.pickle(types.MethodType, _pickle_method, _unpickle_method)


def generate_data_list(folder_dataset, verbose=1):
    """
    Construction of the data list from the data set
    This function return a list of directory (in folder_dataset) in which the contrast is present.
    :return data:
    """
    list_subj = []

    # each directory in folder_dataset should be a directory of a subject
    for subject_dir in os.listdir(folder_dataset):
        if not subject_dir.startswith('.') and os.path.isdir(folder_dataset + subject_dir):
            # data_subjects.append(folder_dataset + subject_dir + '/')
            list_subj.append(subject_dir)

    if not list_subj:
        sct.printv('ERROR: No subject data were found in ' + folder_dataset + '. '
                   'Please organize your data correctly or provide a correct dataset.',
                   verbose=verbose, type='error')

    return list_subj


def read_database(folder_dataset, specifications=None, fname_database='', verbose=1):
    """
    Read subject database from xls file.
    Parameters
    ----------
    folder_dataset: path to database
    specifications: field-based specifications for subject selection
    fname_database: fname of XLS file that contains database
    verbose:

    Returns
    -------
    subj_selected: list of subjects selected
    """
    # initialization
    subj_selected = []

    # if fname_database is empty, check if xls or xlsx file exist in the database directory.
    if fname_database == '':
        sct.printv('  Looking for an XLS file describing the database...')
        list_fname_database = glob.glob(folder_dataset+'*.xls*')
        if list_fname_database == []:
            sct.printv('WARNING: No XLS file found. Returning empty list.', verbose, 'warning')
            return subj_selected
        elif len(list_fname_database) > 1:
            sct.printv('WARNING: More than one XLS file found. Returning empty list.', verbose, 'warning')
            return subj_selected
        else:
            fname_database = list_fname_database[0]
            # sct.printv('    XLS file found: ' + fname_database, verbose)

    # read data base file and import to panda data frame
    sct.printv('  Reading XLS: ' + fname_database, verbose, 'normal')
    try:
        data_base = pd.read_excel(fname_database)
    except:
        sct.printv('ERROR: File '+fname_database+' cannot be read. Please check format or get help from SCT forum.', verbose, 'error')
    #
    # correct some values and clean panda data base
    # convert columns to int
    to_int = ['gm_model', 'PAM50', 'MS_mapping']
    for key in to_int:
        data_base[key].fillna(0.0).astype(int)
    #
    for key in data_base.keys():
        # remove 'unnamed' columns
        if 'Unnamed' in key:
            data_base = data_base.drop(key, axis=1)
        # duplicate columns with lower case names and with space in names
        else:
            data_base[key.lower()] = data_base[key]
            data_base['_'.join(key.split(' '))] = data_base[key]
    #
    ## parse specifications
    ## specification format: "center=unf,twh:pathology=hc:sc_seg=t2"
    list_fields = specifications.split(':')
    dict_spec = {}
    for f in list_fields:
        field, value = f.split('=')
        dict_spec[field] = value.split(',')
    #
    ## select subjects from specification
    # type of field for which the subject should be selected if the field CONTAINS the requested value (as opposed to the field is equal to the requested value)
    list_field_multiple_choice = ['contrasts', 'sc seg', 'gm seg', 'lesion seg']
    list_field_multiple_choice_tmp = copy.deepcopy(list_field_multiple_choice)
    for field in list_field_multiple_choice_tmp:
        list_field_multiple_choice.append('_'.join(field.split(' ')))
    #
    data_selected = copy.deepcopy(data_base)
    for field, list_val in dict_spec.items():
        if field.lower() not in list_field_multiple_choice:
            # select subject if field is equal to the requested value
            data_selected = data_selected[data_selected[field].isin(list_val)]
        else:
            # select subject if field contains the requested value
            data_selected = data_selected[data_selected[field].str.contains('|'.join(list_val)).fillna(False)]
    #
    ## retrieve list of subjects from database
    database_subj = ['_'.join([str(center), str(study), str(subj)]) for center, study, subj in zip(data_base['Center'], data_base['Study'], data_base['Subject'])]
    ## retrieve list of subjects from database selected
    database_subj_selected = ['_'.join([str(center), str(study), str(subj)]) for center, study, subj in zip(data_selected['Center'], data_selected['Study'], data_selected['Subject'])]

    # retrieve folders from folder_database
    list_folder_dataset = [i for i in os.listdir(folder_dataset) if os.path.isdir(folder_dataset+i)]

    # loop across folders
    for ifolder in list_folder_dataset:
        # check if folder is listed in database
        if ifolder in database_subj:
            # check if subject is selected
            if ifolder in database_subj_selected:
                subj_selected.append(ifolder)
        # if not, report to user
        else:
            sct.printv('WARNING: Subject '+ifolder+' is not listed in the database.', verbose, 'warning')

    return subj_selected


def process_results(results, subjects_name, function, folder_dataset, parameters):
    try:
        results_dataframe = pd.concat([result[2] for result in results])
        results_dataframe.loc[:, 'subject'] = pd.Series(subjects_name, index=results_dataframe.index)
        results_dataframe.loc[:, 'script'] = pd.Series([function] * len(subjects_name), index=results_dataframe.index)
        results_dataframe.loc[:, 'dataset'] = pd.Series([folder_dataset] * len(subjects_name), index=results_dataframe.index)
        results_dataframe.loc[:, 'parameters'] = pd.Series([parameters] * len(subjects_name), index=results_dataframe.index)
        return results_dataframe
    except KeyboardInterrupt:
        return 'KeyboardException'
    except Exception as e:
        sct.printv('Error on line {}'.format(sys.exc_info()[-1].tb_lineno), 1, 'warning')
        sct.printv(str(e), 1, 'warning')
        sys.exit(2)


def function_launcher(args):
    import importlib
    # append local script to PYTHONPATH for import
    sys.path.append('{}/testing'.format(os.getenv('SCT_DIR')))
    script_to_be_run = importlib.import_module('test_' + args[0])  # import function as a module
    try:
        output = script_to_be_run.test(*args[1:])
    except:
        import traceback
        print('%s: %s' % ('test_' + args[0], traceback.format_exc()))
        # output = (1, 'ERROR: Function crashed', 'No result')
        from pandas import DataFrame
        status_script = 1
        output_script = 'ERROR: Function crashed.'
        output = (status_script, output_script, DataFrame(data={'status': int(status_script), 'output': output_script}, index=['']))
    return output
    # return script_to_be_run.test(*args[1:])


def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def get_list_subj(folder_dataset, data_specifications=None, fname_database=''):
    """
    Generate list of eligible subjects from folder and file containing database
    Parameters
    ----------
    folder_dataset: path to database
    data_specifications: field-based specifications for subject selection
    fname_database: fname of XLS file that contains database

    Returns
    -------
    list_subj: list of subjects
    """
    if data_specifications is None:
        list_subj = generate_data_list(folder_dataset)
    else:
        print 'Selecting subjects using the following specifications: ' + data_specifications
        list_subj = read_database(folder_dataset, specifications=data_specifications, fname_database=fname_database)
    print "  Number of subjects to process: " + str(len(list_subj))

    # if no subject to process, raise exception
    if len(list_subj) == 0:
        raise Exception('No subject to process. Exit function.')

    return list_subj


def run_function(function, folder_dataset, list_subj, parameters='', nb_cpu=None, verbose=1):
    """
    Run a test function on the dataset using multiprocessing and save the results
    :return: results
    # results are organized as the following: tuple of (status, output, DataFrame with results)
    """

    # add full path to each subject
    data_subjects = [sct.slash_at_the_end(folder_dataset + i, 1) for i in list_subj]

    # All scripts that are using multithreading with ITK must not use it when using multiprocessing on several subjects
    os.environ["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = "1"

    # create datasets with parameters
    import itertools
    data_and_params = itertools.izip(itertools.repeat(function), data_subjects, itertools.repeat(parameters))

    # Computing Pool for parallel process, distribute2mpi.MpiPool in MPI environment, multiprocessing.Pool otherwise
    pool = Pool(nb_cpu)

    try:
        compute_time = time()
        async_results = pool.map_async(function_launcher, data_and_params)
        pool.close()
        pool.join()  # waiting for all the jobs to be done
        compute_time = time() - compute_time
        all_results = async_results.get()
        results = process_results(all_results, list_subj, function, folder_dataset, parameters)  # get the sorted results once all jobs are finished

    except KeyboardInterrupt:
        print "\nWarning: Caught KeyboardInterrupt, terminating workers"
        pool.terminate()
        pool.join()
        # return
        # raise KeyboardInterrupt
        # sys.exit(2)
    except Exception as e:
        sct.printv('Error on line {}'.format(sys.exc_info()[-1].tb_lineno), 1, 'warning')
        sct.printv(str(e), 1, 'warning')
        pool.terminate()
        pool.join()
        # raise Exception
        # sys.exit(2)

    return {'results': results, "compute_time": compute_time}


def get_parser():
    # Initialize parser
    parser = msct_parser.Parser(__file__)

    # Mandatory arguments
    parser.usage.set_description("")
    parser.add_option(name="-f",
                      type_value="str",
                      description="Function to test.",
                      mandatory=True,
                      example="sct_propseg")

    parser.add_option(name="-d",
                      type_value="folder",
                      description="Dataset directory.",
                      mandatory=True,
                      example="dataset_full/")

    parser.add_option(name="-p",
                      type_value="str",
                      description="Arguments to pass to the function that is tested. Please put double-quotes if there are spaces in the list of parameters.\n"
                                  "Image paths must be contains in the arguments list.",
                      mandatory=False)

    parser.add_option(name="-subj",
                      type_value="str",
                      description="Choose the subjects to process based on center, study, [...] to select the testing dataset\n"
                                  "Syntax:  field_1=val1,val2:field_2=val3:field_3=val4,val5",
                      example="center=unf,twh:gm_model=0:contrasts=t2,t2s",
                      mandatory=False)

    parser.add_option(name="-subj-file",
                      type_value="file",
                      description="Excel spreadsheet containing database information (center, study, subject, demographics, ...). If this field is empty, it will search for an xls file located in the database folder. If no xls file is present, all subjects will be selected.",
                      default_value='',
                      mandatory=False)

    parser.add_option(name="-cpu-nb",
                      type_value="int",
                      description="Number of CPU used for testing. 0: no multiprocessing. If not provided, "
                                  "it uses all the available cores.",
                      mandatory=False,
                      default_value=1,
                      example='42')

    parser.add_option(name="-log",
                      type_value='multiple_choice',
                      description="Redirects Terminal verbose to log file.",
                      mandatory=False,
                      example=['0', '1'],
                      default_value='1')

    parser.add_option(name='-email',
                      type_value=[[','], 'str'],
                      description='Email information to send results. Fields are assigned with "=" and are separated with ",":\
\nemail_to: address to send email to\
\nemail_from: address to send email from (default value is: spinalcordtoolbox@gmail.com)\
\npasswd_from: password for email_from',
                      mandatory=False,
                      default_value='')

    parser.add_option(name="-v",
                      type_value="multiple_choice",
                      description="Verbose. 0: nothing, 1: basic, 2: extended.",
                      mandatory=False,
                      example=['0', '1', '2'],
                      default_value='1')

    return parser


# ====================================================================================================
# Start program
# ====================================================================================================
if __name__ == "__main__":

    # initialization
    addr_from = 'spinalcordtoolbox@gmail.com'

    # get parameters
    print_if_error = True  # print error message if function crashes (could be messy)
    parser = get_parser()
    arguments = parser.parse(sys.argv[1:])
    function_to_test = arguments["-f"]
    path_data = sct.slash_at_the_end(os.path.expanduser(arguments["-d"]), slash=1)
    parameters = ''
    if "-p" in arguments:
        parameters = arguments["-p"]
    data_specifications = None
    if "-subj" in arguments:
        data_specifications = arguments["-subj"]
    if "-subj-file" in arguments:
        fname_database = arguments["-subj-file"]
    else:
        fname_database = ''  # if empty, it will look for xls file automatically in database folder
    nb_cpu = None
    if "-cpu-nb" in arguments:
        nb_cpu = arguments["-cpu-nb"]
    create_log = int(arguments['-log'])
    if '-email' in arguments:
        create_log = True
        send_email = True
        # loop across fields
        for i in arguments['-email']:
            if 'addr_to' in i:
                addr_to = i.split('=')[1]
            if 'addr_from' in i:
                addr_from = i.split('=')[1]
            if 'passwd_from' in i:
                passwd_from = i.split('=')[1]
    else:
        send_email = False
    verbose = int(arguments["-v"])

    # start timer
    start_time = time()
    # create single time variable for output names
    output_time = strftime("%y%m%d%H%M%S")

    # build log file name
    if create_log:
        file_log = 'results_test_' + function_to_test + '_' + output_time
        fname_log = file_log + '.log'
        handle_log = sct.ForkStdoutToFile(fname_log)
    print('Testing started on: ' + strftime("%Y-%m-%d %H:%M:%S"))


    # fetch SCT version
    install_type, sct_commit, sct_branch, version_sct = sct.get_sct_version()
    print 'SCT version/commit/branch: ' + version_sct + '/' + sct_commit + '/' + sct_branch

    # check OS
    platform_running = sys.platform
    if (platform_running.find('darwin') != -1):
        os_running = 'osx'
    elif (platform_running.find('linux') != -1):
        os_running = 'linux'
    print 'OS: ' + os_running + ' (' + platform.platform() + ')'

    # check hostname
    print 'Hostname:', platform.node()

    # Check number of CPU cores
    from multiprocessing import cpu_count
    # status, output = sct.run('echo $ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS', 0)
    print 'CPU cores: ' + str(cpu_count())  # + ', Used by SCT: '+output

    # check RAM
    sct.checkRAM(os_running, 0)

    # display command
    print '\nCommand: "' + function_to_test + ' ' + parameters
    print 'Dataset: ' + path_data

    # test function
    try:

        # retrieve subjects list
        list_subj = get_list_subj(path_data, data_specifications=data_specifications, fname_database=fname_database)

        # during testing, redirect to standard output to avoid stacking error messages in the general log
        if create_log:
            handle_log.pause()

        # run function
        tests_ret = run_function(function_to_test, path_data, list_subj, parameters=parameters, nb_cpu=None, verbose=1)
        results = tests_ret['results']
        compute_time = tests_ret['compute_time']

        # after testing, redirect to log file
        if create_log:
            handle_log.restart()

        # build results
        pd.set_option('display.max_rows', 500)
        pd.set_option('display.max_columns', 500)
        pd.set_option('display.width', 1000)
        results_subset = results.drop('script', 1).drop('dataset', 1).drop('parameters', 1).drop('output', 1)
        results_display = results_subset

        # save panda structure
        if create_log:
            results_subset.to_pickle(file_log + '.pickle')

        # mean
        results_mean = results_subset.query('status != 200 & status != 201').mean(numeric_only=True)
        results_mean['subject'] = 'Mean'
        results_mean.set_value('status', float('NaN'))  # set status to NaN
        # results_display = results_display.append(results_mean, ignore_index=True)

        # std
        results_std = results_subset.query('status != 200 & status != 201').std(numeric_only=True)
        results_std['subject'] = 'STD'
        results_std.set_value('status', float('NaN'))  # set status to NaN
        # results_display = results_display.append(results_std, ignore_index=True)

        # count tests that passed
        count_passed = results_subset.status[results_subset.status == 0].count()
        count_crashed = results_subset.status[results_subset.status == 1].count()
        # count tests that ran
        count_ran = results_subset.query('status != 200 & status != 201').count()['status']

        # results_display = results_display.set_index('subject')
        # jcohenadad, 2015-10-27: added .reset_index() for better visual clarity
        results_display = results_display.set_index('subject').reset_index()

        # display general results
        print '\nGLOBAL RESULTS:'

        print 'Duration: ' + str(int(round(compute_time))) + 's'
        # display results
        print 'Passed: ' + str(count_passed) + '/' + str(count_ran)
        print 'Crashed: ' + str(count_crashed) + '/' + str(count_ran)
        # build mean/std entries
        dict_mean = results_mean.to_dict()
        dict_mean.pop('status')
        dict_mean.pop('subject')
        print 'Mean: ' + str(dict_mean)
        dict_std = results_std.to_dict()
        dict_std.pop('status')
        dict_std.pop('subject')
        print 'STD: ' + str(dict_std)

        # print detailed results
        print '\nDETAILED RESULTS:'
        print results_display.to_string()
        print 'Status Legend - 0: Passed | 1: Crashed | 99: Failed | 200: Input file(s) missing | 201: Ground-truth file(s) missing'

        if verbose == 2:
            import seaborn as sns
            import matplotlib.pyplot as plt
            from numpy import asarray

            n_plots = len(results_display.keys()) - 2
            sns.set_style("whitegrid")
            fig, ax = plt.subplots(1, n_plots, gridspec_kw={'wspace': 1}, figsize=(n_plots * 4, 15))
            i = 0
            ax_array = asarray(ax)

            for key in results_display.keys():
                if key not in ['status', 'subject']:
                    if ax_array.size == 1:
                        a = ax
                    else:
                        a = ax[i]
                    data_passed = results_display[results_display['status'] == 0]
                    sns.violinplot(x='status', y=key, data=data_passed, ax=a, inner="quartile", cut=0,
                                   scale="count", color='lightgray')
                    sns.swarmplot(x='status', y=key, data=data_passed, ax=a, color='0.3', size=4)
                    i += 1
            if ax_array.size == 1:
                ax.set_xlabel(ax.get_ylabel())
                ax.set_ylabel('')
            else:
                for a in ax:
                    a.set_xlabel(a.get_ylabel())
                    a.set_ylabel('')
            plt.savefig('fig_' + file_log + '.png', bbox_inches='tight', pad_inches=0.5)
            plt.close()

    except Exception as err:
        if print_if_error:
            print err

    # stop file redirection
    # message = handle_log.read()
    handle_log.close()

    # send email
    if send_email:
        print '\nSending email...'
        # open log file and read content
        with open(fname_log, "r") as fp:
            message = fp.read()
        # send email
        sct.send_email(addr_to=addr_to, addr_from=addr_from, passwd_from=passwd_from, subject=file_log, message=message, filename=fname_log, html=True)
        # handle_log.send_email(email=email, passwd_from=passwd, subject=file_log, attachment=True)
        print 'Email sent!\n'
