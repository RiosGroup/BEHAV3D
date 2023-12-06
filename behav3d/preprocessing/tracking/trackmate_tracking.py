# https://imagej.net/plugins/trackmate/scripting/trackmate-detectors-trackers-keys
# TODO https://github.com/yfukai/laptrack
import sys
import imagej as ij
import scyjava as sj
import pandas as pd
import numpy as np
import psutil
from pathlib import Path
import yaml
from tifffile import imread, imwrite
from skimage.measure import regionprops_table
import argparse
import time
from behav3d import format_time
from behav3d.preprocessing import convert_segments_to_tracks

def run_trackmate_tcells(config, metadata, verbose=False):
    run_trackmate(config,metadata, cell_type="tcells", verbose=verbose)

def run_trackmate_organoids(config, metadata, verbose=False):
    run_trackmate(config, metadata, cell_type="organoids", verbose=verbose)
      
def run_trackmate(
    metadata,
    output_dir=None,
    config=None, 
    cell_type="tcells", 
    verbose=False):
    
    assert config is not None or output_dir is not None, "Either 'config' or 'output_dir' must be supplied"

    if output_dir is None:
        output_dir = config['output_dir']
    
    for _, sample in metadata.iterrows():
        start_time = time.time()
        sample_name = sample['sample_name']
        img_outdir = Path(output_dir, "images", sample_name)
        track_outdir = Path(output_dir, "trackdata", sample_name)
        
        if not img_outdir.exists():
            img_outdir.mkdir(parents=True)
        if not track_outdir.exists():
            track_outdir.mkdir(parents=True)
            
        element_size_x=sample['pixel_distance_xy']
        element_size_y=sample['pixel_distance_xy'] 
        element_size_z=sample['pixel_distance_z']
        element_size_unit=sample['distance_unit']
        
        time_interval = sample['time_interval']
        time_unit = sample['time_unit']
        print(f"--------------- Tracking ({cell_type}) : {sample_name} ---------------")
        print("- Running TrackMate...")
        ### Track the data using TrackMate
        segments_path = Path(img_outdir, f"{sample_name}_{cell_type}_segments.tiff")   
        df_tracks=trackmate_tracking(
            image_path=str(segments_path),
            element_size_x=element_size_x,
            element_size_y=element_size_y,
            element_size_z=element_size_z,
            element_size_unit=element_size_unit,
            verbose=verbose
            )
        # Add 1 to every TrackID so 0 is not a track in the image (should be background)
        df_tracks["TrackID"]=df_tracks["TrackID"]+1
        df_tracks = df_tracks.sort_values(by=["TrackID","position_t"])
        
        tracks_out_path = Path(track_outdir, f"{sample_name}_{cell_type}_tracks.csv")
        df_tracks.to_csv(tracks_out_path, sep=",", index=False)
        
        ### Assign the tracks to existing segments
        # Loop through spots, link to segments in the image and replace label with TrackID
        tcell_tracked_out_path= Path(img_outdir, f"{sample_name}_{cell_type}_tracked.tiff")
        convert_segments_to_tracks(
            tracks_out_path,
            segments_path,
            outpath=tcell_tracked_out_path,
            element_size_z=element_size_z,
            element_size_y=element_size_y,
            element_size_x=element_size_x
        )
        
        end_time = time.time()
        h,m,s = format_time(start_time, end_time)
        print(f"### DONE - elapsed time: {h}:{m:02}:{s:02}\n")
        
def trackmate_tracking(
    image_path,
    element_size_x, 
    element_size_y, 
    element_size_z,
    element_size_unit,
    verbose=False
    ):

    available_75perc_memory=int(psutil.virtual_memory().available*0.75/(1024**3))
    print(f"Setting {available_75perc_memory} Gb of memory based on available memory")
    sj.config.add_options(f'-Xmx{available_75perc_memory}g')
    imagej = ij.init('sc.fiji:fiji', mode='headless')
    
    IJ = imagej.IJ
    WindowManager=imagej.WindowManager
    Model = sj.jimport('fiji.plugin.trackmate.Model')
    Settings = sj.jimport('fiji.plugin.trackmate.Settings')
    TrackMate = sj.jimport('fiji.plugin.trackmate.TrackMate')
    SelectionModel = sj.jimport('fiji.plugin.trackmate.SelectionModel')
    SparseLAPTrackerFactory = sj.jimport('fiji.plugin.trackmate.tracking.jaqaman.SparseLAPTrackerFactory')
    LabelImageDetectorFactory = sj.jimport('fiji.plugin.trackmate.detection.LabelImageDetectorFactory')
    FeatureFilter = sj.jimport('fiji.plugin.trackmate.features.FeatureFilter')
    Logger = sj.jimport('fiji.plugin.trackmate.Logger')
    jint = sj.jimport("java.lang.Integer")
    
    imp = IJ.openImage(image_path)
    IJ.run(imp, "Properties...", f"unit={element_size_unit} pixel_width={element_size_x} pixel_height={element_size_y} voxel_depth={element_size_z}")
    # imp.show()
    
    #----------------------------
    # Create the model object now
    #----------------------------
    
    # Some of the parameters we configure below need to have
    # a reference to the model at creation. So we create an
    # empty model now.
    
    model = Model()
    model.setLogger(Logger.IJ_LOGGER)
    
    settings = Settings(imp)
    
    # Configure detector - We use the Strings for the keys
    settings.detectorFactory = LabelImageDetectorFactory()
    settings.detectorSettings = settings.detectorFactory.getDefaultSettings()
    
    # https://imagej.net/plugins/trackmate/scripting/trackmate-detectors-trackers-keys
    
    settings.trackerFactory = SparseLAPTrackerFactory()
    settings.trackerSettings = settings.trackerFactory.getDefaultSettings()
    settings.trackerSettings['ALLOW_TRACK_SPLITTING'] = False
    settings.trackerSettings['SPLITTING_MAX_DISTANCE'] = 15.0
    # settings.trackerSettings['SPLITTING_FEATURE_PENALTIES'] = []
    
    settings.trackerSettings['ALLOW_TRACK_MERGING'] = False
    settings.trackerSettings['MERGING_MAX_DISTANCE'] = 15.0
    # settings.trackerSettings['MERGING_FEATURE_PENALTIES'] = []
    
    settings.trackerSettings['ALTERNATIVE_LINKING_COST_FACTOR'] = 1.05

    settings.trackerSettings['ALLOW_GAP_CLOSING'] = True
    settings.trackerSettings['MAX_FRAME_GAP'] = jint(5.0)
    settings.trackerSettings['GAP_CLOSING_MAX_DISTANCE'] = 60.0
    settings.trackerSettings['GAP_CLOSING_FEATURE_PENALTIES']["VISIBILITY"]=3.0
    settings.trackerSettings['GAP_CLOSING_FEATURE_PENALTIES']["QUALITY"]=0.75
    
    settings.trackerSettings['LINKING_MAX_DISTANCE'] = 45.0
    settings.trackerSettings['LINKING_FEATURE_PENALTIES']["RADIUS"] = 2.0
    # Add ALL the feature analyzers known to TrackMate. They will 
    # yield numerical features for the results, such as speed, mean intensity etc.
    # settings.addAllAnalyzers()
    
    # Configure track filters - e.g. We want to get rid of the two immobile spots at
    # the bottom right of the image. Track displacement must be above 10 pixels.
    
    # filter2 = FeatureFilter('TRACK_DISPLACEMENT', 10, True)
    # settings.addTrackFilter(filter2)
    
    trackmate = TrackMate(model, settings)
    ok = trackmate.checkInput()
    if not ok:
        print(str(trackmate.getErrorMessage()))
        # sys.exit(str(trackmate.getErrorMessage()))
    
    ok = trackmate.process()
    if not ok:
        sys.exit(str(trackmate.getErrorMessage()))

    # The feature model, that stores edge and track features.
    fm = model.getFeatureModel()

    keys_df_spots = [
        "TrackID",
        "SegmentID",
        "position_t",
        "position_z",
        "position_y",
        "position_x",
    ]
    df_spots = pd.DataFrame(columns=keys_df_spots)
    # Iterate over all the tracks that are visible.
    for trackid in model.getTrackModel().trackIDs(True):
        track = model.getTrackModel().trackSpots(trackid)
        for spot in track:
            sid = spot.ID()
            # q=spot.getFeature('QUALITY')
            spot_info = {
                "TrackID":trackid,
                "SegmentID":spot.ID(),
                "position_t":spot.getFeature("POSITION_T"),
                "position_z":spot.getFeature("POSITION_Z"),
                "position_y":spot.getFeature("POSITION_Y"),
                "position_x":spot.getFeature("POSITION_X"),
            }
            new_row = pd.DataFrame(spot_info, index=[0])
            df_spots = pd.concat([df_spots, new_row], ignore_index=True)
    
    imp.close()       
    imagej.window().clear()
    imagej.getContext().dispose()
    return(df_spots)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser = argparse.ArgumentParser(description='Input parameters for automatic data transfer.')
    parser.add_argument('-c', '--config', type=str, help='path to a config.yml file that stores all required paths', required=False)
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose', required=False)
    args = parser.parse_args()
    with open(args.config, "r") as parameters:
        config=yaml.load(parameters, Loader=yaml.SafeLoader)
    metadata = pd.read_csv(config["metadata_csv_path"])
    verbose=args.verbose
    run_trackmate(config, metadata, verbose)
