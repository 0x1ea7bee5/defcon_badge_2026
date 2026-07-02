# Investigatory data processing
This subdirectory contains python functions + scripts to perform signal processing and plotting for data. You must make this code as memory and computationally efficient as possible. You may reference some of the older code in archive to help you out. However, do not let the old code take precedent over the new tasks I have asked you to complete. Please follow the coding conventions in CLAUDE.md


## Folders:
plot_types/ - directory containing python files for each type of data plot.

## Core Files:
rare_est.py - currently contains code to do rare-l AOA estimation
rx_data.py - responsible for retrieving data over serial, and adding it to a efficient data store for plotting and DSP.
dsp.py - responsible for basic dsp required for plotting
live_plot.py - live plotting file
plot_existing_data.ipynb - interactive jupyter notebook for plotting saved data. Should have maintain parity with live plot; should plot all of the specific plot types.
config.yaml - configuration file used by live plot and plotting jupyter notebook

### Core File functions

rx_data.py - this should receive the serialized CBF and CSI data sent by the telemetry process on the esp32. You may reference how this is done in archive/plot_telemetry.py, as that implementation is generally fine. Please make rx_data.py as efficient as possible. This file will be used by a different python file for live plotting and data saving.

dsp.py - this should do a lot of different kinds of non-specific DSP from data provided by rx_data.py, or even saved data passed as an argument. This file should be able to:

- (denoise) (antialias) Apply denoising and antialiasing filters (reference the implementation used in archive/plot_telemetry.py). Denoising and antialiasing should be enabled by default. These filters should be separate.
- (remove_singularity) throws away data that is computed from a ratio if the denominator is below the denominator threshold. This is to prevent too large of values during plotting.
- (compute_vv_star) Computes the matrix VV*
- (compute_vv_star_ss) Separates V into its unique spatial stream columns, and computes v_nv_n*, where v_n is the column corresponding to a single spatial stream. 
    So for example

    V = [v_0, v_1]
    Should yield 
    v_0v_0* and v_1v_1*
- (vv_star_ratio) (vv_star_ratio_ss) Computes the ratio between unique VV* combinations. This should be easily applicable for separated spatial stream v_nv_n*. For the example VV*:
    00
    10 11
    20 21 22
    30 31 32 33
    The following is computed:
    10/00
    20/00
    30/00
    21/11
    31/11
    32/22

These functionalities should be applicable across multiple subcarriers (effectively the V tensor returned from the CBF, and should be pretty well optimized.)

rare_est.py - this is generally fine, but I need you to make the following modifications:
    Create a function that is able to estimate the array geometry from incoming data,  based on a subspace based self calibration process where you minimize the projection residual: 
    min_{pos}  Σ_k || P_noise · a_model(az_k, el_k, pos) ||²
    
    I also want you to clean up this file a bit to conform to the coding guidelines I have mentioned. I want to be able to easily feed data into the self-calibration function and then feed data + estimated array geometry into the rare-l algorithm.


config.yaml - this file has the following variables:
apply_antialias: true - applies the antialiasing filter to the data
apply_smooth_fiolter: true - applies the smoothing filter to the data
plot_window: 200 - the plot widow size for live plotting
denom_ratio: 0.01 -  minimum denominator ratio


## General Plot files
The following files will contain generic definitions for plot types. The characteristics of these plots will be inherited for other types of plots.
Base plot characteristics:
    - If a plot has legends, the user must be able to click the legend item to hide the trace associated with the legend item.
    - All plots should have a sliding window that plots the last N samples. Let N=200 and be configurable.
    - The plot title should include the mac address that is currently being displayed.
plot_types/waterfalls.py
    This type of plot will plot phase and magnitude waterfalls over subcarriers for a given mac address. This plot type should support multiple subplots with multiple columns/rows. This plot also needs to have a labeled color bar.
plot_types/complex_plane.py
    This type of plot will plot the provided data in the complex plane over time. Newer data points should be more opaque than older data points. Data from different "antenna" columns should be plotted as separate legend items, and should be plotted with different shapes (stars,circles,diamonds,etc). Each new point should be connected to older points with a half opacity dashed line. This plot should have a checkbox labeled "plot all subcarriers". When this box is checked, all subcarriers should be plotted simultaneously, with each subcarrier given a unique color corresponding to a continuous color map. When this box is unchecked, only a single subcarrier should be plotted at a time. There should be a subcarrier slider that selects the subcarrier to plot when the "plot all subcarriers" box is unchecked. When the box is checked, there should be two sliders-- one for the lower range of subcarriers to plot, and one for the upper range of subcarriers to plot. This plot type should support multiple subplots with multiple rows/columns. The complex plots should autoscale. There should also be a time slider that goes from one to the max window size in the config yaml that allows the user to adjust the time window size.
plot_types/time_series.py
    This will be a simple time series plot that plots the provided data over time with a nice time-dependent color map. This plot type should support multiple subplots with multiple rows/columns.
plot_types/histograms.py
    This will be a simple histogram plot that changes as new data is added. This plot type should support multiple subplots with multiple rows/columns.

## Specific plot files
The following files will contain more specific types of plots. These specific plot types should be contained within the special_plots.py file. These plot files / functions should be flexible enough to be used with the static file plotter notebook, as well as the live plotter.
- VV* waterfall (VVH_waterfall) - from VV*,  use the waterfall.py plot type. Each waterfall subplot should be the lower diagonal of the VV* matrix
- VV* complex plane plot (VVH_cplx) - from VV*, use the complex_plane plot type for VV*. Each subplot should be the lower diagonal of the VV* matrix
- vv* spatial stream waterfall (VVH_ss_waterfall) - from the individual collection of spatial stream v_nv_n*,  use the waterfall.py plot type. Each waterfall subplot should be the lower ual sdiagonal of the v_nv_n* matrix. There should be a spatial stream slider.
- vv* spatial stream complex plane plot (VVH_ss_cplx) - from the individual collection of spatial stream v_nv_n*, use the complex_plane plot type for v_nv_n*. Each subplot should be the lower diagonal of the v_nv_n* matrix. There should be a spatial stream slider.
- VV* ratio waterfall (VVH_ratio_waterfall) - should be similar to VVH_waterfall, but should instead plot from vv_star_ratio
- VV* ratio complex plane plot (VVH_ratio_cplx) - should be similar to VVH_cplx, but should instead plot from vv_star_ratio.
- vv* ratio spatial stream waterfall (VVH_ratio_ss_waterfall) - should be similar to VVH_ss_waterfall, but should instead plot from vv_star_ratio_ss
- vv* spatial stream complex plane plot (VVH_ratio_ss_cplx)- should be similar to VVH_ss_cplx, but should instead plot from vv_star_ratio.
- Estimated Array +AOA plot (est_array_plot) - as defined in the rare_est.py file, there is a function that should allow for the exact array geometry to be estimated. This plot should have a subplot on the left that plots the estimated array geometry in 3d space. The subplots on the right should plot the azimuth angle over time (top) and elevation angle over time(bottom) from the rare-l algorithm. The rare-l algorithm should be using the geometry from the estimation. Both should update in real time
The VVH and VVH ratio + spatial stream variants should have subplot formats that are lower triangular. So the subplots should all be square. The subplots should be large.


## Live plot file (live_plot.py)
This file needs to create an interactive applet with a bunch of buttons. Each button needs to correspond to each of the unique specific plot file types. When these buttons are pressed, a window the specific plot should pop up, and begin live plotting. There needs to be an additional "save data" button that, when pressed, generates csvs with the format <timestamp>_CSI_INFO.csv and <timestamp>_CBF_INFO.csv, and saves them to the database/collected_data folder. Please refer to archive/plot_telemetry.py for the format of these csvs. Add necessary helper functions to handle the combination of DSP and plotting. There should be an additional drop down list that allows for the user to select a valid mac address (based on whether or not a cbf was received), for which the next plotting button clicked will open the plot for that mac address.

## saved plot file (plot_existing_data.ipynb)
This file needs to be an interactive jupyter notebook that can read the files saved from the live plot "save data" button, and plot all of the specific plot files. This notebook should maintain parity with the live plotter. There should be a time slider that allows for the user to mimic behavior over time.






## Edits that I needed to ask claude to make:
- Please consolidate the ratio functions defined in the dsp file and the ratio functions defined in the special plots file. The ratio computation functions should be located in the dsp file.