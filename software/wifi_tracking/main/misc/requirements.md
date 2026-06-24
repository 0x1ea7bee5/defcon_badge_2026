# Misc sub-directory
This file has definitions and requirements misc subdirectory

## Structure
Currently WIP. Right now doing exploratory testing.

I need you to update the telemetry.h and telemetry.c files to allow for easy serialization of data. I plan to use this to serialize data (specifically eigenvectors and eigenvalues from the V matrix) and send it to a python script to get plotted

I need one callback/function to do serialization

I need another callback/function that serializes the compressed beamforming matrix, mac address, snr per spatial stream, cbf eigenvectors and cbf eigenvalues.

I need you to add the callback/function that serializes the compressed beamforming matrix to the main.c file.

Additionally, I need you to create a python script that can read this serial data, and do live plotting. I want you to plot the following:
 

on the right pane of the screen, I need you to plot for every single unique mac address and every single subcarrier. I need you to have a subcarrier slider to avoid crowding the plot, and each location on the subcarrier slider should correspond to a unique plot for that subcarrier. If one mac address has the subcarrier for a specific slider value and another doesn't, only plot the data for the mac address that has the subcarrier. For each mac address, I will need you to plot for each spatial stream. I want each mac address to be given a unique colors, and I want each eigenvector to be differentiated by a unique line style (for a given mac address). I need you to treat each eigenvector as a unique spatial path, and plot each element in an eigenvector as a point on the complex plane. Points within a vector should be the same color and same linestyle. 


On the left pane of the screen, you are free to plot whatever you may deem useful for understanding channel state.


Addendum to plotting code:
I would like for you to replace the eigenvalue vs subcarrier subplot with the following subplot:

I want this subplot to be similar to the right pane subplot, but instead of having a subcarrier slider, I would like for the slider to be for each unique mac address, and I would like for each eigenvector to be a unique color, and each element in the eigenvector a unique linestyle. You will plotting every subcarrier.


Addendum:

for plot_telemetry,
I want you to hide/prevent window 1 from plotting (the window with non-waterfal plots). I want you to create a new window that plots the following:

Suppose I receive a cbf matrix with the following information for a subcarrier:
My channel was defined by a transmitter with 4 antennas and a receiver with 2 antennas (H was 2x4)


[v11 v12 v13 v14] # spatial stream1
[v21 v22 v23 v24] # spatial stream2


I need you to treat each spatial stream separately. I need you to compute the following ratios

[v12/v11 v13/v11 v14/v11] # spatial stream 1
[v22/v21 v23/v21 v24/v21] # spatial stream 2

I then need you to create a winndow for plotting. On this window, you will divide the window into Nss columns (2 in this case). For each column, you will plot the associated phase and magnitude of the computed ratios from above. Each row will be an entry in each spatial stream vector. For this specific example, there will 4 columns (phase_ss1 mag_ss1 phase_ss2 mag_ss2) and three rows. for each of the subplots, you will plot a waterfall, with the x axis being the subcarrier index. As before, please include a mac address slider.





Addendum:

for plot_telemetry,
From the previous waterfall request, you have this example

[v11 v12 v13 v14] # spatial stream1
[v21 v22 v23 v24] # spatial stream2


I need you to treat each spatial stream separately. I need you to compute the following ratios

[v12/v11 v13/v11 v14/v11] # spatial stream 1
[v22/v21 v23/v21 v24/v21] # spatial stream 2

I need you to create new a window for plotting. On this window, you will divide the window into Nss columns (2 in this case). For each column, you will plot the associated phase and magnitude of the computed ratios from above. Each row will be an entry in each spatial stream vector. Instead of a waterfall plot, you will plot the ratios on the complex plane. As time increases, you will add another point to the complex plane. I need you to add a subcarrier and mac address slider for this plot.


Please update this last plot to cycle through rainbow dots over time so i can denote time by color. addiktionally, make the memory window for this plot be 100 samples. Also, connect each dot with a faint dotted line.





For the original csi plot window, I want you to remove the line subplot with each mac addressed over it, and replace that subplot with a polar plot, similar to the previous onne i requested. the subcarrier slider should control each subcarrier. 




for plot_telemetry,
I want you to create another plotting window. 

Suppose I receive a cbf matrix with the following information for a subcarrier:
My channel was defined by a transmitter with 4 antennas and a receiver with 2 antennas (H was 2x4)


[v11 v12 v13 v14] # spatial stream1
[v21 v22 v23 v24] # spatial stream2


I want you to take the ratio of each element between streams.
plot [v11/v21 v12/v22 v13/v23 v14/v24]

For this example there should be 4 columns. As in the previous plots, I would like for you to plot each ratio as a complex point on the real imaginary plane, have the same mac address filter, and same subcarrier filter.





Addendum:
for plot_telemetry.py,
I need you to create new a window for plotting. You will divide the window into the number of subplots required to create plots for each vector of the beamforming matrix.
you will plot each value in the associated vector on the complex plane. As time increases, you will add another point to the complex plane. I need you to add a subcarrier and mac address slider for this plot. See some of the previous plotting code for more insight



Addendum: 
for plot telemetry.py
I want you to create a new window for plotting. There will be a subcarrier filter and a mac address filter. I want you split the subplots into rows and columns based on the number of TX and RX antennas. Each subplot should be a unique point in the matrix. I want each subplot to plot the magnitude and phase over time, as a connected graph. The plot should be a 1d plot with a separate magnitude and phase subplot. The points should be connected.


Addendum: 
for plot_telemetry.py, please create a window that plots the gram matrix over time. I want the plot to have two subplots: one that plots the matrix where the drawn image is the newest matrix, and the other plot plots the average over the last 100 samples. Make sure to have a mac slider and subcarrier slider.

Addendum:
Please apply an optional denoising filter for all of the CBF related plots. I want to be able to enable / disable the noise filter with a DENOISE=true/false flag at the top of the python file


Addendum:
For the cbf ratios, i need you to discard points where the mangitude of the denominator is less than DENOM_LIM=0.01. I want DENOM_LIM to be at the top of the python plotting file. Also, make each individual antenna / legend label in the polar plots hideable. Also add the ability to zoom in on the polar plots.


Addendum
for plot_telemetry.py, could you please create a new window that plots the ifft waterfall the of the CSI plot. Keep it similar to the other CSI plot. Also please remove the cbf ratio plots -- cbf ratio waterfalls and cbf ratio polar plots


Addendum
Great. please create a new file called rare_est.py in the same directory as plot_telemetry.py. I want the contents of this file to be imported into plot_telemetry.py. I want this file to handle phase estimation using RARE-L. All RARE-L estimation math should be done in this file, and there should be a detailed explanation at the top. Once this file is completed, I need you to do rare-l estimation on the incoming CBF data. I then need you to create two plots to plot the output of this rare-l estimation: one is a complex plane plotter, similar to existing plots in the plot_telemetry.py file. The other plot is a waterfall plot, similar to existing plots. be sure to add appropriate sliders


addendum:
please hide figure 1, figure 2, figure 6 figure 5 and figure 4. please add another figure that plots the azimuth and elevation angles over time 