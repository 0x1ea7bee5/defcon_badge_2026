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