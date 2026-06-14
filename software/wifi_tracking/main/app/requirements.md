# App Subdirectory
This file has definitions and requirements for the apps subdirectory


## Structure
I plan on using FreeRTOS to handle scheduling. All tasks are going to be defined in this folder. These tasks will communicate with queues and semaphores. There are going to be the following tasks:

Radio Manager Task - This will handle switching between bluetooth, wifi client control, and wifi packet sniffing control. It will share a queue with the sniffer Task, the Wifi Client task, and the bluetooth task. Data from these three tasks will be dumped to this queue, will be consumed by the Radio Manager Task, and 

Sniffer Task - Will handle sniffing data collection. Will pass sniffing data to the DSP task. 


UI Task - This task will also be responsible for user facing things (button tracking, joystick tracking, writing to the OLED display, etc.)

App state Task - This task will keep track of the app state (ie, does the user switch from bluetooth to wifi client to wifi sniffer,etc.; does the user move the joystick to select a different channel, etc.). This task will coordinate all other tasks.


Motor Control Task - Will read motor state and drive motor driver hardware.

DSP Task  - This will handle processing of radio state information + advanced state estimation. This task will receive information from the motor control task, the radio manager task (state about which mode we are in), and sniffer

