The way the `claude` file was produced was by taking the original file (`claude-original`) and simply adding a `false && (...original_condition...)`
to the condition surrounding the error message `... Root ... for security reasons ...`, i.e. commenting out that exit path :)

This info is potentially relevant for upgrading to a newer version of claude code, because that has to be done manually now
