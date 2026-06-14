# UX subdirectoru
This file has definitions and requirements for the ux sub-directory. THe files in this folder are intended to define components that will be used in a larger UX task that associates app state with UX state, and allows for signalling from UX control elements to update app state.


## Structure
- `/main/ux/display/` - folder containing files for display rendering code
- `/main/ux/menu/` - folder containing files for menu control and menu state
- `/main/ux/display/display_ctl.c/h` - display controller, provides simple and powerful functions and callbacks to draw to the display.
- `/main/ux/display/display_ctl.c/h`
- `/main/ux/display/menu_ctl.c/h` - menu controller, provides callbacks and functions to update the display based on menu and graphics state.
- `/main/ux/button_ctl.c/h` - provides simple callbacks/functions to read menu button state
- `/main/ux/joystick_ctl.c/h` - provides simple callbacks/functions to read joystick information



## Function / Design interfaces

### display_ctl.c/h
These files should heavily use the esp_lcd library 
These files should also have a definition for the display in use. The display that will be used is a 128X32 oled display from adafruit.

display_init() - initialize the display
display_on() - function or callback that turns the lcd display on
display_off() - function or callback that turns the lcd display off
display_reset() - resets the lcd display
display_sleep() - put the lcd display into sleep mode

draw_screen



### menu_ctl.c/h
These files should reference functions in the display_ctl.c/h files for drawing to the display, as well as the lvgl library for graphics elements. These files should define a menu data structure that contains pointers to graphics elements, such as menu buttons and keyboards.





### button_ctl.c/h


### joystick_ctl.c/h