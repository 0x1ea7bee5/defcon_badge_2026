# Packet Scanner sub-directory
This file has definitions and requirements for the wifi sub-directory


## Structure
- wifi_defs.h - header file containing all packet and frame fields and subfields required for this project. Is mostly for storing wifi related constants
- packet_scanner.c - file containing all wifi packet scanning logic and field extraction
- packet_scanner.h - file containing function declarations for packet_scanner.c
- bluetooth.c - file containing code required for bluetooth connectivity
- bluetooth.h - file containing function declarations for bluetooth.c
- wifi_client.c - file containing code required for esp32 to behave as a wifi client
- wifi_client.h - file containing function declarations for wifi_client.c


## Function / Design interfaces

### packet_scanner.c
These files are to handle packet processing and field extraction. I will define the required top level functions / callbacks here with a description of what I need them to do. You are free to define as many other functions and callbacks as needed. You are free to decide the argument types and return type of these functions/callbacks as needed, but I would like for you to explain your reasoning when you update / create a documentation file, as well as explain any pitfalls with what you have chosen. Please use good coding practices and etiquette with the header files.

scan_for_cbf - This callback/function should filter for action and action no ack frames that contain the compressed beamforming matrix. This callback needs to be able to filter VHT beamforming packets, HE beamforming packets, HE SU-MIMO and HE MU-MIMO. This function should return some sort of data structure that provides info about the mac address, the sounding dialog token, the receive timestamp, SNR per spatial stream information, and the givens rotation angles per subcarrier, rssi. Feel free to include any other information that you believe may be useful for estimating the channel state. 

scan_for_ndpa - This callback/function should filter for ndpa frames. It should return a some sort of data structure with info about the source mac, destination mac, and sounding dialog token.

scan_for_ssid - This callback/function should filter for beacon frames. It should return the capabilities of the AP (ht, vht, he, supported rates, etc), the timestamp of the packet reception, the ssid, the source mac address

start_monitor - puts the esp32 into monitor mode. user should be able to provide a channel

switch_channel - Should allow for the user to switch the channel during sniffing. This will be used later to allow for scanning to be done on multiple channels in rapid sequence.

stop_monitor - takes esp32 out of monitor mode to allow for other radio functions to be used later.

scan_for_csi - This callback/function should specifically use the CSI function of the ESP32 to get channel state information. This callback/function should return some sort of data structure containing the channel state information, source mac address, destination mac address, rssi. Feel free to include any other information that you believe may be useful for estimating the channel state. 


### wifi_client.c / wifi_client.h
These files will handle wifi client connections. This file should allow for channel scans, client connections, and data transmission / connection to the internet if necessary. The requirements for this file are a little less strict, but there should be the following capabilities:

- Esp32 should be able to scan channels for available APs, and should be able to report those ssids to other parts of the codebase

- ESP32 should be able to connect to an ssid, and should be able to access the internet

- ESP32 should fallow all normal wifi requirements

I would like some sort of callbacks or functions for channel scanning, association, dissociation, and data transfer.

Please use good coding practices and etiquette with the header files.

### bluetooth.c / bluetooth.h
Still haven't determined the requirements for these files yet. They can be skipped for now. 