#!/usr/bin/env python3

import enum
import os
import regex
import sys
import typing
import xml.sax.saxutils

# Set boilerplate mandated by the regex package.
# See https://pypi.python.org/pypi/regex/
regex.DEFAULT_VERSION = regex.VERSION1

"""
A file that converts shorthand, that for the sake
of slapping a silly name on it, called SKIT
(Short Keyboard-operated InterText),

The syntax of the 'language' this file expects is quite simple,
as it parses on a line-by-line basis with the starting character
indicating to the parser what the text following the character should be
parsed as.

If the following text is not what was expected, then an error is raised
and parsing will stop.

The syntax is as follows:

Starting characters:

#   - Comment to include in output
;   - Comment to exclude from output
Fn  - Function
Mem - Memory allocation block
Res - Resource allocation block
Pod - Podtype tag
Def - Define tag
Con - Container block

Note that comments may not be placed within other tag definitions
themselves, but may be placed anywhere outside of them, as of writing.

---- Functions ----

Functions can have the following tags:

- const
- nr [t|f] (noreturn true or false)
- li (leak ignore)
- pure
- rv <type> (return value)
- ur (use return value)
- any line starting in a 0-9 combination specifies a function argument

Function arguments can have the following tags:

- fmt or fmt{printf|scanf} (specifies the argument is a format string type. Omitting {} assumes printf-style)
- min{type,...}
  - Where type may be one of:
    - argvalue
    - mul (this expects two following arguments, all others expect one)
    - sizeof
    - strlen
- nb (not bool)
- nn (not null)
- nu (not uninitialized)
- s  (null-terminated string)
- v{comma-separated ranges} (valid ranges for a numeric argument)

For example, a function can be like:

Fn Name
   li
   ur
   rv int
   nr f
   1 nb nn nu v{0:10}
   2 fmt{scanf} min{argvalue,1} nn nu s

Which is equivalent to:

<function name="Name">
  <leak-ignore/>
  <use-retval/>
  <returnValue type="int"/>
  <noreturn>false</noreturn>
  <arg nr="1"><not-bool/><not-null/><not-uninit/><valid>0:10</valid></arg>
  <arg nr="2"><formatstr type="scanf"/><minsize type="argvalue" arg="1"/><not-null/><not-uninit/><strz/><arg/>
</function>

A 'v' tag is used to specify a range restriction on a numeric
argument to a function. Each range is comma-separated. For example,
if we wanted to indicate that an parameter may only have values between
0 to 10 inclusive (on both ends of the range), this can be done like so:

v{0:10}

Now assume that an argument has two valid ranges that it may fall within,
say 0 to 10, or 50 to 60; this is specified like so:

v{0:10,50:60}

If an allowed value isn't part of a range, then the colon just needs to be omitted.
For example, assume an argument can only be within 0 to 10, or be 15 this is specified
like:

v{0:10,15}

To specify that a value may be from a certain value, to the end of the range
of the argument's type, this can be specified by not providing a value on
one side of a colon like so:

v{:10} 

which indicates "the value must be in the range from whatever the min value of the
type is (inclusive) to 10 (inclusive)"

or

v{10:}

which indicates "the value must in the range from 10 (inclusive) to whatever the
max value of the type is (inclusive)"

Note: a newline must follow a function specification in order to end it.
      the only exception to this is if a function specification is at the
      end of a file and nothing else follows it.

---- Podtype definitions ----

Podtype definitions define as the name state a POD (plain-old-data) type.

For example, say you had the C fixed-width type int32_t and wanted to define it
in a cppcheck file so you didn't need to point cppcheck at a C implementation's
headers (which usually take forever to parse), then the following could be done:

pod int32_t s 4

which is equivalent to:

<podtype name="int32_t" sign="s" size="4"/>

Note that the sign and size specifiers are optional and can be omitted.
This is desirable when defining structs, which don't have a sign.
For example:

struct Point2D {
    int32_t x;
    int32_t y;
};

can be represented as both:

pod Point2D

or:

pod Point2D 8

with the latter providing more information to cppcheck

---- Define tags ----

Define tags specify the equivalent of C preprocessor defines.

For example:

#define CONSTANT 4

would be:

def CONSTANT 4

which is equivalent to writing:

<define name="CONSTANT" value="4"/>

It's important to keep in mind that whatever is within the value
attribute position will be copied verbatim (aside from the script
automatically escaping any XML reserved characters, like <, >, &, ', and ").

---- Memory/Resource allocation blocks ----

Allocation blocks make cppcheck aware of functions that allocate
memory in a manner similar to the malloc family of functions
(unfortunately it does not track memory from functions that allocate
memory into an out parameter). So, if a function in an API is of the
form:

// It's not a requirement to have void parameters, this is just for
// sake of example.
some_data_type* create_data(void) { 
    struct some_data_type* data = calloc(1, sizeof(*data));

    if (!data) {
        return NULL
    }

    // ... some other code ...
    return data;
}

void free_data(struct some_data_type* data) {
    // ... free other members of data if they're allocated ...

    // free data itself
    free(data);
}

then it can be tracked with a memory allocation block. This
would be modeled like so:

mem
  alloc init create_data
  dealloc free_data

'init' in the allocation tag specifier tells cppcheck that the
function also initializes the returned data, and that it
should not assume usages after the call are potentially
operating on uninitialized data (i.e. avoiding false-positives).
If a function does *not* initialize the returned data, this should
not be specified.

Resource blocks are similar to memory allocation blocks but are
intended to represent system resources that are acquired by a
program (for example, file descriptors returned by fopen() or the POSIX open()).
These would be specified like so:

res
  alloc fopen
  dealloc fclose


Note that memory and resource blocks are allowed to contain more than
one allocation and deallocation tag specifier, which enables grouping
families of allocation/deallocation functions together. For example,
assume malloc() and calloc() were the only allocation functions and free()
is the only deallocation function. This can be specified as:

mem
  alloc malloc
  alloc init calloc
  dealloc free

A newline must follow an allocation block in order to end it, unless it's 
the last entry in a file.
"""


class ParseError(Exception):
    """
    Exception class used to signify an error
    that has occurred during parsing.
    """
    pass


# These entries are kind of poorly named.
# They refer to 'level' indentation as in:
#
# Fn name          <- Function-level
#   nr t           <- Function-entry-level
#
# and
#
# mem              <- Function-level
#  alloc malloc    <- Function-entry-level
#  dealloc free
class Indentation(str, enum.Enum):
    """Indentation that should be applied for different entities"""

    """Indentation for function level entries"""
    FUNCTION = "    "
    """Indentation for function-scope entries"""
    FUNCTION_ENTRY = "        "


@enum.unique
class LineType(enum.Enum):
    """Describes the possible types of lines to parse input as"""

    """An invalid line"""
    ERROR           = -1
    """Blank line"""
    EMPTY           = 0
    """A line containing a code comment to be included in the generated XML"""
    COMMENT_INCLUDE = 1
    """A line containing a comment to be excluded in the generated XML"""
    COMMENT_EXCLUDE = 2
    """A function definition (<function></function>)"""
    FUNCTION        = 3
    """An allocation block (<memory></memory>)"""
    MEM_ALLOC       = 4
    """A resource block (<resource></resource>)"""
    RES_ALLOC       = 5
    """A podtype tag (<podtype name="" sign="s" "size"/>)"""
    PODTYPE         = 6
    """A define tag (<define name="" value=""/>)"""
    DEFINE          = 7
    """A container definition (<container></container>)"""
    CONTAINER       = 8


def determine_line_type(line: str) -> LineType:
    """
    Determines the line type based off the
    initial characters on the line.
    """
    if len(line) == 0:
        return LineType.EMPTY
    if line.startswith("#"):
        return LineType.COMMENT_INCLUDE
    if line.startswith(";"):
        return LineType.COMMENT_EXCLUDE
    if regex.match("^fn", line, regex.IGNORECASE):
        return LineType.FUNCTION
    if regex.match("^mem", line, regex.IGNORECASE):
        return LineType.MEM_ALLOC
    if regex.match("^res", line, regex.IGNORECASE):
        return LineType.RES_ALLOC
    if regex.match("^pod", line, regex.IGNORECASE):
        return LineType.PODTYPE
    if regex.match("^def", line, regex.IGNORECASE):
        return LineType.DEFINE
    if regex.match("^con", line, regex.IGNORECASE):
        return LineType.CONTAINER

    return LineType.ERROR


@enum.unique
class AllocationLineType(enum.Enum):
    """Describes the types of entries allowed within an allocation block"""

    """An empty line or EOL"""
    EMPTY        = 0
    """An allocation function specification"""
    ALLOC_FUNC   = 1
    """A deallocation function specification"""
    DEALLOC_FUNC = 2


def determine_allocation_line_type(line: str) -> AllocationLineType:
    """
    Determines the type of a line within an allocation
    block based off the starting characters on the line.
    """
    if len(line) == 0:
        return AllocationLineType.EMPTY
    if regex.match("^alloc", line, regex.IGNORECASE):
        return AllocationLineType.ALLOC_FUNC
    if regex.match("^dealloc", line, regex.IGNORECASE):
        return AllocationLineType.DEALLOC_FUNC

    raise ParseError("Invalid allocation entry line '{}' encountered.".format(line))


@enum.unique
class FunctionLineType(enum.Enum):
    """Describes the allowed line types within a function block."""

    """An empty line. This acts as a terminator"""
    EMPTY       = 0
    """Pure tag (<pure/>)"""
    PURE        = 1
    """Const tag (<const/>)"""
    CONST       = 2
    """Use return value tag (<use-retval/>)"""
    USE_RETVAL  = 3
    """Return type tag (<returnValue type=""/>) """
    RETURN_TYPE = 4
    """Leak ignore tag (<leak-ignore/>)"""
    LEAK_IGNORE = 5
    """Noreturn tag (<noreturn></noreturn>)"""
    NORETURN    = 6
    """Argument tag (<arg nr="N"></arg>)"""
    ARGUMENT    = 7


def determine_function_line_type(line: str) -> FunctionLineType:
    """
    Determines the type of line within a function based off
    the starting characters on the line.
    """
    if len(line) == 0:
        return FunctionLineType.EMPTY
    if regex.match("^pure", line, regex.IGNORECASE):
        return FunctionLineType.PURE
    if regex.match("^const", line, regex.IGNORECASE):
        return FunctionLineType.CONST
    if regex.match("^ur", line, regex.IGNORECASE):
        return FunctionLineType.USE_RETVAL
    if regex.match("^rv", line, regex.IGNORECASE):
        return FunctionLineType.RETURN_TYPE
    if regex.match("^li", line, regex.IGNORECASE):
        return FunctionLineType.LEAK_IGNORE
    if regex.match("^nr", line, regex.IGNORECASE):
        return FunctionLineType.NORETURN
    if regex.match("^[0-9]+", line, regex.IGNORECASE):
        return FunctionLineType.ARGUMENT

    raise ParseError("Invalid function line type '{}' encountered.".format(line))


@enum.unique
class ArgumentAttributeType(enum.Enum):
    """The types of attributes that may be used with argument specifiers."""

    """Format string tag (<formatstr type="printf|scanf"/> defaults to printf if type is omitted)"""
    FORMAT_STR       = 0
    """Minimum size tag (<minsize type="" arg=""/>)"""
    MIN_SIZE         = 1
    """Not bool tag (<not-bool/>)"""
    NOT_BOOL         = 2
    """Not null tag (<not-null/>)"""
    NOT_NULL         = 3
    """Not uninitialized tag (<not-uninit/>)"""
    NOT_UNINIT       = 4
    """Null-terminated string tag (<strz/>)"""
    NULL_TERM_STRING = 5
    """Valid range tag (<valid></valid>)"""
    VALID_RANGE      = 6


def determine_argument_attribute_type(attribute: str) -> ArgumentAttributeType:
    """
    Determines an argument attribute type based on the initial
    part of its name.
    """
    if regex.match("^fmt", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.FORMAT_STR
    if regex.match("^min", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.MIN_SIZE
    if regex.match("^nb", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.NOT_BOOL
    if regex.match("^nn", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.NOT_NULL
    if regex.match("^nu", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.NOT_UNINIT
    if regex.match("^s", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.NULL_TERM_STRING
    if regex.match("^v", attribute, regex.IGNORECASE):
        return ArgumentAttributeType.VALID_RANGE

    raise ParseError("Invalid argument attribute '{}' encountered.".format(attribute))


# Retrieves the text between {}
def retrieve_match_between_braces(line: str):
    return regex.search("(?<={)[^}]*(?=})", line)


class Parser:
    """
    A really silly parser class for the input strings.

    I have no actual implementation knowledge of language parsers
    so I apologize in advance if this looks awful.
    """

    def __init__(self, lines: typing.List[str], out_file: typing.IO[str]):
        self.lines = lines
        self.out_file = out_file
        self.line_no = 0

    # Writes the beginning XML of a cppcheck cfg file.
    def write_file_begin(self) -> None:
        self.out_file.write(("<?xml version=\"1.0\"?>\n"
                             "<def format=\"2\">\n"))

    # Writes the ending XML of a cppcheck cfg file.
    def write_file_end(self) -> None:
        self.out_file.write("</def>")

    # Creates a <returnValue> tag for a function
    def compose_return_value_string(self, line: str) -> str:
        tokens = line.split(" ", maxsplit=1)
        if len(tokens) < 2:
            raise ParseError("Malformed function return value tag '{}'. Missing return type.".format(line))

        return "<returnValue type=\"{}\">\n".format(tokens[1])

    # Creates a <noreturn> tag for a function
    def compose_noreturn_string(self, line: str) -> str:
        tokens = line.split(" ")
        if len(tokens) < 2:
            raise ParseError("Malformed noreturn tag '{}'. Missing 't' or 'f'.".format(line))
        if len(tokens) > 2:
            expected_str = tokens[0] + " " + tokens[1]
            raise ParseError("Excess arguments in noreturn tag. Expected '{}' but saw '{}'."
                             .format(expected_str, line))

        operand = tokens[1].lower()
        if operand == "t":
            operand = "true"
        elif operand == "f":
            operand = "false"
        else:
            raise ParseError("Malformed noreturn tag '{}'. Argument is not 't' or 'f'.".format(line))

        return "<noreturn>{}</noreturn>\n".format(operand)

    # Creates a <formatstr/> tag for a function argument
    def compose_formatstr_string(self, token: str) -> str:
        if regex.fullmatch("fmt", token, regex.IGNORECASE):
            return "<formatstr/>"

        specifier_match = retrieve_match_between_braces(token)
        if specifier_match is None:
            raise ParseError("Malformed format specifier: '{}'. Possibly missing a brace."
                             .format(token))

        specifier = specifier_match[0].lower()
        if len(specifier) == 0 or specifier not in ["printf", "scanf"]:
            raise ParseError(("Malformed format specifier. "
                              "Specifier in a format string must be either 'printf' or 'scanf', "
                              "but saw '{}' instead.".format(specifier)))
        return "<formatstr type=\"{}\"/>".format(specifier)

    # Creates a <minsize/> tag for a function argument
    def compose_minsize_string(self, token: str) -> str:
        specifier_match = retrieve_match_between_braces(token)
        if specifier_match is None:
            raise ParseError("Malformed minsize specifier '{}'.".format(token))

        args = [item.strip().lower() for item in specifier_match[0].split(",")]
        args_len = len(args)

        if args_len < 2:
            raise ParseError("Missing argument in minsize specifier: '{}'."
                             .format(token))

        # Sanitize argument type
        if args[0] not in ["argvalue", "mul", "sizeof", "strlen"]:
            raise ParseError(("Invalid minsize argument type. Must be one of: "
                              "'argvalue, 'mul', 'sizeof', 'strlen', but saw '{}'."
                              .format(args[0])))

        is_mul_type = args[0] == "mul"

        if not is_mul_type and args_len > 2:
            expected_str = "min{" + args[0] + "," + args[1] + "}"
            raise ParseError("Excess elements in minsize specifier. Expected '{}', but saw '{}'."
                             .format(expected_str, token))

        if is_mul_type and args_len > 3:
            expected_str = "min{" + args[0] + "," + args[1] + "," + args[2] + "}"
            raise ParseError("Excess elements in minsize specifier. Expected '{}', but saw '{}'."
                             .format(expected_str, token))

        if not all(arg.isdigit() for arg in args[1:]):
            raise ParseError("Arguments in a minsize specifier must be decimal integral values.")

        if is_mul_type:
            return "<minsize type=\"{}\" arg=\"{}\" arg2=\"{}\"/>".format(args[0], args[1], args[2])

        return "<minsize type=\"{}\" arg=\"{}\"/>".format(args[0], args[1])

    # Creates a valid range string for a function argument (<valid></valid>)
    def compose_valid_range_string(self, token: str) -> str:
        specifier_match = retrieve_match_between_braces(token)
        if specifier_match is None:
            raise ParseError("Malformed valid range specifier: '{}'".format(token))

        # TODO: Could probably try sanitizing this syntax more.
        specifier = specifier_match[0].lower()
        if len(specifier) == 0:
            raise ParseError("A valid range specifier may not be empty.")

        return "<valid>{}</valid>".format(specifier_match[0])

    # Creates an argument string for a function definition
    def compose_argument_string(self, line: str) -> str:
        tokens = line.split(" ")
        argument = "<arg nr=\"{}\">".format(tokens[0])
        for token in tokens[1:]:
            attribute_type = determine_argument_attribute_type(token)
            if attribute_type == ArgumentAttributeType.FORMAT_STR:
                argument += self.compose_formatstr_string(token)
            elif attribute_type == ArgumentAttributeType.MIN_SIZE:
                argument += self.compose_minsize_string(token)
            elif attribute_type == ArgumentAttributeType.NOT_BOOL:
                argument += "<not-bool/>"
            elif attribute_type == ArgumentAttributeType.NOT_NULL:
                argument += "<not-null/>"
            elif attribute_type == ArgumentAttributeType.NOT_UNINIT:
                argument += "<not-uninit/>"
            elif attribute_type == ArgumentAttributeType.NULL_TERM_STRING:
                argument += "<strz/>"
            elif attribute_type == ArgumentAttributeType.VALID_RANGE:
                argument += self.compose_valid_range_string(token)

        argument += "</arg>\n"
        return argument

    # Parses a memory or resource allocation tag
    def parse_alloc(self, alloc_type: LineType) -> None:
        composed_alloc = Indentation.FUNCTION
        end_alloc = Indentation.FUNCTION
        if alloc_type == LineType.MEM_ALLOC:
            composed_alloc += "<memory>\n"
            end_alloc += "</memory>\n"
        elif alloc_type == LineType.RES_ALLOC:
            composed_alloc += "<resource>\n"
            end_alloc += "</resource>\n"

        # Keep going until an empty line
        has_alloc = False
        has_dealloc = False
        while True:
            self.line_no += 1

            # Hit the end of the file
            if self.line_no >= len(self.lines):
                if not has_alloc or not has_dealloc:
                    raise ParseError("Allocation block hit end-of-file before being fully defined.")
                break

            next_line = self.lines[self.line_no].strip()
            line_type = determine_allocation_line_type(next_line)

            if line_type == AllocationLineType.EMPTY:
                break

            tokens = next_line.split(" ")
            tokens_len = len(tokens)

            # Deduplicating this would be nice...
            if line_type == AllocationLineType.DEALLOC_FUNC and tokens_len > 2:
                raise ParseError(("Excess elements in deallocation entry.\n"
                                  "Expected at most 2 arguments but saw "
                                  "{}: {}".format(tokens_len, tokens)))
            elif line_type == AllocationLineType.ALLOC_FUNC and tokens_len > 3:
                raise ParseError(("Excess elements in allocation entry.\n"
                                  "Expected at most 3 arguments but saw "
                                  "{}: {}".format(tokens_len, tokens)))

            composed_alloc += Indentation.FUNCTION_ENTRY

            if line_type == AllocationLineType.ALLOC_FUNC:
                has_alloc = True
                composed_alloc += "<alloc"
                if tokens[1] == "init":
                    if len(tokens) < 3:
                        raise ParseError(("Missing function name in allocation block: '{}'.\n"
                                          "Note: 'init' is used to specify that the allocation "
                                          "function also initializes the allocated data.".format(next_line)))

                    composed_alloc += " init=\"true\">" + tokens[2]
                else:
                    composed_alloc += " init=\"false\">" + tokens[1]

                composed_alloc += "</alloc>\n"
            elif line_type == AllocationLineType.DEALLOC_FUNC:
                has_dealloc = True
                composed_alloc += "<dealloc>" + tokens[1] + "</dealloc>\n"

        if not has_alloc:
            raise ParseError("An allocation block cannot be defined without an allocation function.")
        if not has_dealloc:
            raise ParseError("An allocation block cannot be defined within a deallocation function.")

        composed_alloc += end_alloc
        self.out_file.write(composed_alloc)

    # Parses a podtype definition tag
    def parse_podtype(self, line: str) -> None:
        tokens = line.split(" ")
        tokens_len = len(tokens)

        if tokens_len < 2:
            raise ParseError(("Missing name in podtype definition tag. "
                              "podtype definitions must at least have a name."))

        if tokens_len > 4:
            raise ParseError(("Excess arguments in podtype definition tag. "
                              "podtype definitions require only a name. "
                              "podtype definitions may also have an optional sign, "
                              "and an optional size specified as well."))

        def handle_argument(token: str) -> str:
            if token not in ["s", "u"]:
                if token.isdigit():
                    return " size=\"{}\"".format(token)

                raise ParseError("Invalid podtype argument value '{}'".format(token))
            else:
                return " sign=\"{}\"".format(lower_sign)

        podtype_str = "{}<podtype name=\"{}\"".format(Indentation.FUNCTION, tokens[1])
        if tokens_len >= 3:
            lower_sign = tokens[2].lower()
            podtype_str += handle_argument(lower_sign)

        if tokens_len == 4:
            podtype_str += handle_argument(tokens[3])

        podtype_str += "/>\n"
        self.out_file.write(podtype_str)

    # Parses a define tag
    def parse_define(self, line: str) -> None:
        tokens = line.split(maxsplit=2)
        tokens_len = len(tokens)

        if tokens_len < 3:
            raise ParseError(("Malformed define tag. Definition tags must have a "
                              "name and value."))

        # Escape the value string, as C uses some characters that are special
        # in XML that need escaping (notably, < > ' " &). By default escape()
        # only escapes < > and & by default, so we need to add the apostrophe
        # and quotation mark.
        define_name = tokens[1]
        define_value = xml.sax.saxutils.escape(tokens[2], entities={
                "'": "&apos;",
                "\"": "&quot;"
            }).strip()

        define_str = "{}<define name=\"{}\" value=\"{}\"/>\n" \
                     .format(Indentation.FUNCTION, define_name, define_value)
        self.out_file.write(define_str)

    # Parses a function with the given line as the beginning
    # of the function.
    def parse_function(self, line: str) -> None:
        function_header = line.split(" ")
        if len(function_header) != 2:
            raise ParseError(("Malformed function header. "
                              "Expected function specifier and a following name,"
                              "but saw '{}' instead.".format(line)))

        name = function_header[1]
        composed_function = "{}<function name=\"{}\">\n".format(Indentation.FUNCTION, name)

        # Keep going until an empty line.
        while True:
            self.line_no += 1

            # Hit the end of the file without a newline
            if self.line_no >= len(self.lines):
                break

            next_line = self.lines[self.line_no]
            next_line = next_line.strip()
            line_type = determine_function_line_type(next_line)

            entry_str = ""
            if line_type == FunctionLineType.EMPTY:
                break
            elif line_type == FunctionLineType.PURE:
                entry_str = "<pure/>\n"
            elif line_type == FunctionLineType.CONST:
                entry_str = "<const/>\n"
            elif line_type == FunctionLineType.LEAK_IGNORE:
                entry_str = "<leak-ignore/>\n"
            elif line_type == FunctionLineType.USE_RETVAL:
                entry_str = "<use-retval/>\n"
            elif line_type == FunctionLineType.RETURN_TYPE:
                entry_str = self.compose_return_value_string(next_line)
            elif line_type == FunctionLineType.NORETURN:
                entry_str = self.compose_noreturn_string(next_line)
            elif line_type == FunctionLineType.ARGUMENT:
                entry_str = self.compose_argument_string(next_line)

            composed_function += Indentation.FUNCTION_ENTRY + entry_str
        composed_function += Indentation.FUNCTION + "</function>\n"
        self.out_file.write(composed_function)

    # Determines what 'thing' the line identifies as and hands
    # it off to a dedicated parsing function
    def parse_line(self, line: str) -> None:
        line_type = determine_line_type(line)

        if line_type == LineType.ERROR:
            raise ParseError("Invalid starting sequence '{}'".format(line, self.line_no + 1))
        elif line_type == LineType.EMPTY:
            self.out_file.write("\n")
        elif line_type == LineType.FUNCTION:
            self.parse_function(line)
        elif line_type == LineType.COMMENT_INCLUDE:
            self.out_file.write(Indentation.FUNCTION + "<!-- " + line[1:].strip() + " -->\n")
        elif line_type == LineType.COMMENT_EXCLUDE:
            pass
        elif line_type in [LineType.MEM_ALLOC, LineType.RES_ALLOC]:
            self.parse_alloc(line_type)
        elif line_type == LineType.PODTYPE:
            self.parse_podtype(line)
        elif line_type == LineType.DEFINE:
            self.parse_define(line)
        elif line_type == LineType.CONTAINER:
            pass  # TODO

    def parse_lines(self) -> None:
        self.write_file_begin()

        total_lines = len(self.lines)
        while self.line_no < total_lines:
            self.parse_line(lines[self.line_no])
            self.line_no += 1

        self.write_file_end()


#
# Actual use of the code below
#

if len(sys.argv) < 2:
    print("An input file must be provided.")
    sys.exit(-1)

if len(sys.argv) < 3:
    print("An output filename must be provided.")
    sys.exit(-1)

input_file_path = sys.argv[1]
output_file_path = sys.argv[2]

try:
    with open(input_file_path, encoding="utf-8", mode="r") as in_file, \
         open(output_file_path, encoding="utf-8", mode="w") as out_file:
        lines = in_file.read().split('\n')
        parser = Parser(lines, out_file)
        parser.parse_lines()
except FileNotFoundError:
    print("Unable to find file: {}\nExiting...".format(input_file_path))
    sys.exit(-1)
except OSError as oe:
    print("Unable to open file: {}\nReason:{}\nExiting...".format(input_file_path, oe))
    sys.exit(-2)
except ParseError as pe:
    print("Error at line {}!\n{}".format(parser.line_no + 1, pe))
    sys.exit(-3)

print("Success!\nGenerated XML stored to output file {}".format(os.path.abspath(output_file_path)))
