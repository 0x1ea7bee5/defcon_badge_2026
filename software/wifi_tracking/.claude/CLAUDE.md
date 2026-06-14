# Project WiFi Tracking DEFCON Badge
Code to be running on esp32-c5 defcon badge. App will allow users to navigate menus with a joystick, cursor, and a select button. User will have the ability to select different functions.


## Architecture
- `/main/main.c` - entry point
- `/main/` - all code compiled and run on esp32-c5
- `/main/app/` - app related code to handle user inputs and app state
- `/main/ux/` - ux related code (menus, graphics, etc)
- `/main/tests/` - unit test code and testbenches
- `/main/wifi/` - code related to handling wifi capabilities and packet processing
- `/main/position_control/` - code related to motor position control
- `/main/state_estimation/` - code related to estimating rf channel state, and estimating locations of people and objects
In each of these folders, there will be a document called requirements.md. You must read this document before making any modifications to the code in the subdirectory.


## Coding Conventions
- Functions should have concise comments describing the function, input arguments, and return values. Type information should be included.
- All code must be memory safe: check allocations and properly free resources
- Functions should be as short as possible
- Functions should be simple and do one thing only
- Lines should be less than 79 characters
- Use include guards
- Use consts when possible
- Avoid complicated conditionals
- Code should be reusable and readable
- Use good C coding standards 


## Testing
- All unit tests are to be in Cmock
- Clearly explain test inputs and expected outputs / behaviors
- Aim for >80% coverage

## Documentation
- All documentation should be stored in the documentation folder
- Documentation should be clear and concise.
- You are to update documentation continuously to reflect the state of the codebase. 
- The structure of the documentation folder should match the structure of the files in main (ie, same file names and subdirectories.)



# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, invoke the Skill tool with `skill: "graphify"` before doing anything else.

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.
The python virtual environment is located under python_tools/venv/

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).


