"""
Perform Ilastik segmentation of 4D datasets (czyx) with the following channels:
1 -
2 - 
3 - 
4 - 


Known errors:

If running with verbose=True, there will be an error code for object segmentation:
IndexError: index 1 is out of bounds for axis 0 with size 0

Segmentation is still performed however, and this error can be ignored
"""
import os
import sys
import argparse
import subprocess
import yaml
import h5py

from pathlib import Path
from tifffile import imread, imwrite

import numpy as np
import pandas as pd
import time
from behav3d import format_time

def run_ilastik_segmentation(
    metadata, 
    output_dir = None,
    ilastik_path = None,
    ilastik_pix_clas_model = None,
    ilastik_org_seg_model = None,
    ilastik_tcell_seg_model = None,
    ilastik_org_postproc_model = None,
    ilastik_tcell_postproc_model = None,
    config=None, 
    keep_all=False, 
    verbose=False
    ):
    
    assert config is not None or all(
        [output_dir,
         ilastik_path, 
         ilastik_pix_clas_model, 
         ilastik_org_seg_model, 
         ilastik_tcell_seg_model,
         ilastik_org_postproc_model,
         ilastik_tcell_postproc_model
         is not None]
    ), "Either 'config' or 'output_dir, ilastik_path, ilastik_pix_clas_model, ilastik_org_seg_model, ilastik_tcell_seg_model, ilastik_org_postproc_model and ilastik_tcell_postproc_model' parameters must be supplied"
    
    if not all([
        output_dir,
        ilastik_path, 
        ilastik_pix_clas_model, 
        ilastik_org_seg_model, 
        ilastik_tcell_seg_model,
        ilastik_org_postproc_model,
        ilastik_tcell_postproc_model
        is not None]
        ):
        output_dir = config['output_dir']
        metadata = pd.read_csv(config["metadata_csv_path"])
        ilastik_path = config['ilastik_path']
        ilastik_pix_clas_model = config['ilastik_pixel_classifier_model']
        ilastik_org_seg_model = config['ilastik_organoid_segmentation_model']
        ilastik_tcell_seg_model = config['ilastik_tcell_segmentation_model']
        ilastik_org_postproc_model = config['ilastik_organoid_postprocessing_model']
        ilastik_tcell_postproc_model = config['ilastik_tcell_postprocessing_model']
    
    img_outdir = Path(output_dir, "images")
   
    for _, sample in metadata.iterrows():
        start_time = time.time()
        sample_name = sample['sample_name']
        raw_image_path = sample['raw_image_path']
        img_outdir = Path(output_dir, "images", sample_name)
        if not img_outdir.exists():
            img_outdir.mkdir(parents=True)


        raw_image_path = Path(sample['raw_image_path'])
        raw_h5_path = Path(img_outdir, raw_image_path.stem + "_ilastik_input.h5")

        if raw_h5_path.exists():
            print(f"--------------- Using existing raw_img .h5: {sample_name} ---------------")
            print(raw_h5_path)
        else:
            print(f"--------------- Converting .tiff to .h5: {sample_name} ---------------")
            print(f"(Due to Ilastik not supporting these 5D .tiff files yet)")
            raw_img = imread(raw_image_path)
            
            raw_h5 = h5py.File(name=raw_h5_path, mode="w")
            raw_h5.create_dataset(name="/image", data=raw_img, chunks=True)
            raw_h5.close()
            
        full_raw_image_path=str(raw_h5_path)+"/image"
        
        print(f"--------------- Segmenting: {sample_name} ---------------")       
        ### General pixel classifier
        
        pix_prob_path = Path(img_outdir, f"{sample_name}_probabilities.h5")
        if pix_prob_path.exists():
            print(f"- Already found pixel classifier results")
            print(f"If wanting to redo, delete the *_probabilities.h5")
            print(f"Using {pix_prob_path}")
        else:
            print("- Running the ilastik pixel classifier...")
            pix_prob_path=run_ilastik_pixel_classifier(
                raw_data=full_raw_image_path,
                model=ilastik_pix_clas_model,
                out_path=pix_prob_path,
                ilastik_path=ilastik_path,
                verbose=verbose
            )

        ### Organoid segmentation
        preseg_org_h5_path = Path(img_outdir, f"{sample_name}_organoid_presegments.h5")
        if preseg_org_h5_path.exists():
            print(f"- Already found initial organoid segmentation results")
            print(f"If wanting to redo, delete the *_organoids_presegments.h5")
            print(f"Using {preseg_org_h5_path}")
        else:
            print("- Performing initial organoid segmentation...")
            preseg_org_h5_path=run_ilastik_object_segmentation(
                raw_data=full_raw_image_path,
                pixel_probabilities=pix_prob_path,
                model=ilastik_org_seg_model,
                out_path=preseg_org_h5_path,
                ilastik_path=ilastik_path,
                verbose=verbose
            )

        seg_org_h5_path = Path(img_outdir, f"{sample_name}_organoid_segments.h5")
        if seg_org_h5_path.exists():
            print(f"- Already found postprocessed (splitting) organoid segmentation results")
            print(f"If wanting to redo, delete the *_organoids_segments.h5")
            print(f"Using {seg_org_h5_path}")
        else:
            print("- Performing organoid segment postprocessing (splitting)...")
            seg_org_h5_path=run_ilastik_object_splitter(
                raw_data=full_raw_image_path,
                segments=preseg_org_h5_path,
                model=ilastik_org_postproc_model,
                out_path=seg_org_h5_path,
                ilastik_path=ilastik_path,
                verbose=verbose
            )
            
        seg_org_tiff_path = Path(img_outdir, f"{sample_name}_organoid_segments.tiff")
        if seg_org_tiff_path.exists():
            print(f"- Already found organoid segmentation .tiff file")
            print(f"If wanting to redo, delete the *_organoids_segments.tiff")
            print(f"Using {seg_org_tiff_path}")
        else:
            im = h5py.File(name=seg_org_h5_path, mode="r")["segments"][:].squeeze()
            # im = imread(seg_tcell_out_path)
            
            imwrite(
                seg_org_tiff_path,
                im.astype('uint16'),
                imagej=True,
                metadata={'axes':'TZYX'}
            )

        ### Tcell segmentation
        preseg_tcell_h5_path = Path(img_outdir, f"{sample_name}_tcell_presegments.h5")
        if preseg_tcell_h5_path.exists():
            print(f"- Already found initial T cell segmentation")
            print(f"If wanting to redo, delete the *_tcell_presegments.h5")
            print(f"Using {preseg_tcell_h5_path}")
        else:
            print("- Performing initial T cell segmentation...")
            preseg_tcell_h5_path=run_ilastik_object_segmentation(
                raw_data=full_raw_image_path,
                pixel_probabilities=pix_prob_path,
                model=ilastik_tcell_seg_model,
                out_path=preseg_tcell_h5_path,
                ilastik_path=ilastik_path
            )

        seg_tcell_h5_path = Path(img_outdir, f"{sample_name}_tcell_segments.h5")
        if seg_tcell_h5_path.exists():
            print(f"- Already found postprocessed (splitting) T cell segmentation results")
            print(f"If wanting to redo, delete the *_tcell_segments.h5")
            print(f"Using {seg_tcell_h5_path}")
        else:
            print("- Performing T cell segment postprocessing (splitting)...")
            seg_tcell_h5_path=run_ilastik_object_splitter(
                raw_data=full_raw_image_path,
                segments=preseg_tcell_h5_path,
                model=ilastik_tcell_postproc_model,
                out_path=seg_tcell_h5_path,
                ilastik_path=ilastik_path
            )

        ### Add metadata to the image so TrackMate correctly reads the dimensions
        seg_tcell_tiff_path = Path(img_outdir, f"{sample_name}_tcell_segments.tiff")
        if seg_tcell_tiff_path.exists():
            print(f"- Already found T cell segmentation .tiff file")
            print(f"If wanting to redo, delete the *_tcell_segments.tiff")
            print(f"Using {seg_tcell_tiff_path}")
        else:
            im = h5py.File(name=seg_tcell_h5_path, mode="r")["segments"][:].squeeze()
            # im = imread(seg_tcell_out_path)
            imwrite(
                seg_tcell_tiff_path,
                im.astype('uint16'),
                imagej=True,
                metadata={'axes':'TZYX'}
            )
        
        if not keep_all:
            print("- Removing all intermediate files and keeping only final segment tiffs as '--keep_all' is not supplied...")
            os.remove(pix_prob_path)
            os.remove(preseg_org_h5_path)
            os.remove(seg_org_h5_path)
            os.remove(preseg_tcell_h5_path)
            os.remove(seg_tcell_h5_path)
            os.remove(raw_h5_path)
        
        end_time = time.time()
        h,m,s = format_time(start_time, end_time)
        print(f"### DONE - elapsed time: {h}:{m:02}:{s:02}\n")
        
### Segment the data using Ilastik
def run_bash(script, path_bash="bash", stdin=None):
    """Run a bash-script. Returns (stdout, stderr), raises error on non-zero return code"""
    if sys.version_info[0]<3 or (sys.version_info[0]==3 and sys.version_info[1]<7):
        proc = subprocess.run(
            [path_bash, '-c', script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
            )
    else:
        proc = subprocess.run(
            [path_bash, '-c', script],
            capture_output=True
            )
    stdout, stderr = proc.stdout, proc.stderr
    return(stdout.decode("utf-8") , stderr.decode("utf-8") , proc.returncode)

def run_ilastik_pixel_classifier(
    raw_data, 
    model, 
    out_path=None, 
    output_dir=None, 
    ilastik_path="ilastik",
    verbose=False
    ):
    ## Perform pixel classification
    if out_path is None:
        if output_dir is None:
            out_path = Path(Path(raw_data).parent, f"{Path(raw_data).stem}_probabilities.h5")
        else:
            out_path = Path(output_dir, f"{Path(raw_data).stem}_probabilities.h5")
    command = (
        f"{ilastik_path} --headless "
        f"--project='{model}' "
        # f"--output_format='multipage tiff' "
        # f"--output_filename_format={pix_prob_path} "
        f"--output_format='hdf5' "
        f"--output_internal_path /probabilities "
        f"--output_filename_format={out_path} "
        f"--output_axis_order=ctzyx "
        f"--export_source='Probabilities' "
        f"{raw_data}"
    )
    if verbose:
        print("Running command:")
        print(command)
    
    out, error, returncode = run_bash(command)
    assert returncode==0, f"Pixel classification did not complete correctly\n\nOut:{out}\n\nError:{error}"

    return(out_path)
    
def run_ilastik_object_segmentation(
    raw_data, 
    pixel_probabilities, 
    model, 
    out_path=None, 
    output_dir=None, 
    ilastik_path="ilastik",
    verbose=False
    ):
    if out_path is None:
        if output_dir is None:
            out_path = Path(Path(raw_data).parent, f"{Path(raw_data).stem}_preobjects.h5")
            out_csv_path = Path(Path(raw_data).parent, f"{Path(raw_data).stem}_preobject_features.h5")
        else:
            out_path = Path(output_dir, f"{Path(raw_data).stem}_preobjects.h5")
            out_csv_path = Path(output_dir, f"{Path(raw_data).stem}_preobject_features.h5")

    command = (
        f"{ilastik_path} --headless "
        f"--project='{model}' "
        # f"--output_format='multipage tiff' "
        # f"--output_filename_format={preobj_out_path} "
        f"--output_format='hdf5' "
        f"--output_internal_path /pre_objects "
        f"--output_filename_format={out_path} "
        # f"--table_filename={out_csv_path} "
        f"--raw_data {raw_data} " 
        f"--prediction_maps {pixel_probabilities}/probabilities "

        f"--export_source='object identities' "
    )
    if verbose:
        print("Running command:")
        print(command)
    out, error, returncode = run_bash(command)
    
    assert os.path.isfile(out_path), f"Object segmentation did not complete correctly\n\nOut:\n{out}\n\nError:\n{error}"

    return(out_path)

def run_ilastik_object_splitter(
    raw_data, 
    segments, 
    model, 
    out_path=None, 
    output_dir=None, 
    ilastik_path="ilastik",
    verbose=False
    ):
    if out_path is None:
        if output_dir is None:
            out_path = Path(Path(raw_data).parent, f"{Path(raw_data).stem}_finalobjects.tiff")
        else:
            out_path = Path(output_dir, f"{Path(raw_data).stem}_finalobjects.tiff")

    command = (
        f"{ilastik_path} --headless "
        f"--project='{model}' "
        # f"--output_format='multipage tiff' "
         f"--output_format='hdf5' "
        f"--output_filename_format={out_path} "
        f"--output_internal_path /segments "
        # f"--output_axis_order=zyx "
        f"--raw_data {raw_data} " 
        f"--segmentation_image {segments}/pre_objects "
        f"--export_source='Object-Identities' "
    )
    
    if verbose:
        print("Running command:")
        print(command)
    
    out, error, returncode = run_bash(command)
    assert returncode==0, f"Object splitting did not complete correctly\n\nOut:{out}\n\nError:{error}"
    
    return(out_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser = argparse.ArgumentParser(description='Input parameters for automatic data transfer.')
    parser.add_argument('-c', '--config', type=str, help='path to a config.yml file that stores all required paths', required=False)
    parser.add_argument('-k', '--keep_all', action='store_true', help='Keep original files after checks if compression has been succesful', required=False)
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose', required=False)

    args = parser.parse_args()
    with open(args.config, "r") as parameters:
        config=yaml.load(parameters, Loader=yaml.SafeLoader)
    keep_all=args.keep_all
    metadata = pd.read_csv(config['metadata_csv_path'])
    verbose=args.verbose
    run_ilastik_segmentation(config, metadata, keep_all, verbose)