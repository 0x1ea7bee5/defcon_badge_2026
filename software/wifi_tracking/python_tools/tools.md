# Explaining some of the misc python tools




## CSI phase viewer
I want you to create a file called csi_viewer.py that creates and interactive app that does the following:
* generates a 2d "room" that is a cube, and has an adjustable room size. This room should have an adjustable reflectivity
* generates an adjustable number of wave propagation paths from a movable source in this room. These propagation paths should be simulated, and their wavelength should be adjustable. The number of propagation paths should be low
* Allows for me to add a stick person into the room. this stick person will interact with these. By default, one stick person is in the room. At least one wave path needs to hit / interact with the person.
* There should be one receiver in this room. The location of this receiver should be adjustable. This receiver should plot the phase and amplitude of each wave that hits it on the complex plane. This plot should also plot each wave in the time domain. There should be an additional plot that plots the sum of the signals on the complex plane + the time domain version of this signal.
* This should all be on one window. The left pane should have the interactive room with all the knobs. the right pane should have all the receiver plots. The goal of this applet is to illustrate how the phase changes over a wireless channel + get insight into the channel state of any given room. Add any additional details that you believe would be helpful for gaining better intuition for how movement / position impacts channel state and phases.

Addendum:
I need you to update the app so that there is always at least one path bouncing against the person and being received by the receiver. This needs to be updated as the person moves / walks


ADDendum2:

Please update the csi_viewer.py app to have the complex plane plotters to 