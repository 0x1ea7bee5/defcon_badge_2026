# State estimatino sub-directory
This file has definitions and requirements for the state_estimation sub-directory


## Structure
Currently WIP. Right now doing exploratory testing.
I need you to add the following function to the get_matrices_cbf.c and get_matrices_cbf.h files as appropriate:

reconstruct_v: this function will take the cbf struct defined captured from packet scanner and reconstruct the v matrix from the givens rotation matrices. I will be using this function in main later to print out the reconstructed matrix


## Function / Design interfaces

### packet_scanner.c
