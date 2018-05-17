# cppcheck Configuration Files

A repository for all the cppcheck static analysis configuration files I've made.

### What do these do?

You use them with a static analysis program for C/C++ called [cppcheck](https://github.com/danmar/cppcheck/). What they do is allow cppcheck to assume certain things about functions in an API. For example, say there's a function in an API that can only accept a certain range of values (e.g. only values 0–10 inclusive), you can declare this constraint in a config file and cppcheck's analyzer will be able to flag violations of that constraint (the best it can, of course).

These files also allow leak checking to be more thorough, as you can describe which functions in an API allocate and deallocate and associate them together.

For documentation on the kinds of checking configuration files can help with, consider checking out cppcheck's manual.

### Are these perfect configuration files?

I try to make them as best as I can to prevent false-positives, however there may be the odd case where I've missed or overlooked something. If you think you've found one of those cases, please open an issue about it so it can be fixed or looked into.
