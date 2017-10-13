# cppcheck Configuration Files

A repository for all the cppcheck static analysis configuration files I've made.

### What do these do?

You use them with a static analysis program for C/C++ called [cppcheck](https://github.com/danmar/cppcheck/). What they do is allow cppcheck to assume certain things about functions in an API. For example, in C there are no mild language level protections to enforce that a null pointer shouldn't be passed to a parameter (C++ has references to prevent this more-or-less). By documenting that a parameter should not be passed to a given parameter, cppcheck is then able to flag violations of this precondition.

These files also allow leak checking to be more thorough, as you can describe which functions in an API allocate and deallocate and associate them together.

For documentation on the kinds of checking configuration files can help with, consider checking out cppcheck's manual.

### Are these perfect configuration files?

I try to make them as best as I can to prevent false-positives, however there may be the odd case where I've missed or overlooked something. If you think you've found one of those cases, please open an issue about it so it can be fixed or looked into.
