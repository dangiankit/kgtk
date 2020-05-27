"""Copy records from the first KGTK file to the output file,
expanding lists.

"""

from argparse import ArgumentParser, Namespace
import attr
from pathlib import Path
import sys
import typing

from kgtk.kgtkformat import KgtkFormat
from kgtk.io.kgtkreader import KgtkReader, KgtkReaderOptions
from kgtk.io.kgtkwriter import KgtkWriter
from kgtk.utils.argparsehelpers import optional_bool
from kgtk.value.kgtkvalue import KgtkValue, KgtkValueFields
from kgtk.value.kgtkvalueoptions import KgtkValueOptions

@attr.s(slots=True, frozen=True)
class KgtkExplode(KgtkFormat):
    input_file_path: typing.Optional[Path] = attr.ib(validator=attr.validators.optional(attr.validators.instance_of(Path)))

    output_file_path: typing.Optional[Path] = attr.ib(validator=attr.validators.optional(attr.validators.instance_of(Path)))

    column_name: str = attr.ib(validator=attr.validators.instance_of(str))

    prefix: str = attr.ib(validator=attr.validators.instance_of(str))
                               
    field_names: typing.List[str] = attr.ib(validator=attr.validators.deep_iterable(member_validator=attr.validators.instance_of(str),
                                                                                    iterable_validator=attr.validators.instance_of(list)))

    overwrite_columns: bool = attr.ib(validator=attr.validators.instance_of(bool))
    expand_list: bool = attr.ib(validator=attr.validators.instance_of(bool))
                               
    # TODO: find working validators
    # value_options: typing.Optional[KgtkValueOptions] = attr.ib(attr.validators.optional(attr.validators.instance_of(KgtkValueOptions)), default=None)
    reader_options: typing.Optional[KgtkReaderOptions]= attr.ib(default=None)
    value_options: typing.Optional[KgtkValueOptions] = attr.ib(default=None)

    error_file: typing.TextIO = attr.ib(default=sys.stderr)
    verbose: bool = attr.ib(validator=attr.validators.instance_of(bool), default=False)
    very_verbose: bool = attr.ib(validator=attr.validators.instance_of(bool), default=False)

    def process(self):
        if len(self.column_name) == 0:
            raise ValueError("The name of the column to explode is empty.")

        if self.verbose:
            print("Validate the names of the fields to extract.", file=self.error_file, flush=True)
        if len(self.field_names) == 0:
            raise ValueError("The list of fields to explode is empty.")
        field_name: str
        for field_name in self.field_names:
            if field_name not in KgtkValueFields.FIELD_NAMES:
                raise ValueError("Unknown field name '%s'." % field_name)

        # Open the input file.
        if self.verbose:
            if self.input_file_path is not None:
                print("Opening the input file: %s" % self.input_file_path, file=self.error_file, flush=True)
            else:
                print("Reading the input data from stdin", file=self.error_file, flush=True)

        kr: KgtkReader =  KgtkReader.open(self.input_file_path,
                                          error_file=self.error_file,
                                          options=self.reader_options,
                                          value_options = self.value_options,
                                          verbose=self.verbose,
                                          very_verbose=self.very_verbose,
        )

        if self.verbose:
            print("Check that the source column '%s' is present." % self.column_name, file=self.error_file, flush=True)
        if self.column_name not in kr.column_name_map:
            raise ValueError("Column name '%s' not found in the input file." % self.column_name)
        column_idx: int = kr.column_name_map[self.column_name]

        if self.verbose:
            print("Build the map of exploded columns and list of new column names", file=self.error_file, flush=True)
        explosion: typing.MutableMapping[str, idx] = { }
        column_names: typing.List[str] = kr.column_names.copy()
        for field_name in self.field_names:
            exploded_name: str = self.prefix + field_name
            if self.verbose:
                print("Field '%s' becomes '%s'" % (field_name, exploded_name), file=self.error_file, flush=True)
            if self.exploded_name in explosion:
                raise ValueError("Field name '%s' is duplicated in the field list.")
            if self.exploded_name in kr.column_names:
                if self.overwrite_columns:
                    existing_idx = kr.column_name_map[exploded_name]
                    explosion[field_name] = existing_idx
                    if self.verbose:
                        print("Field '%s' is overwriting existing column '%s' (idx=%d)" % (field_name, exploded_name, existing_idx),
                              file=self.error_file, flush=True)
                else:
                    raise ValueError("Exploded column '%s' already exists and not allowed to overwrite" % exploded_name)
            else:
                column_names.append(exploded_name)
                exploded_idx: int = len(column_names)
                explosion[field_name] = exploded_idx
                if self.verbose:
                    print("Field '%s' becomes new column '%s' (idx=%d)" % (field_name, exploded_name, exploded_idx), file=self.error_file, flush=True)
        new_column_count: int = len(column_names) - kr.column_count
                
        # Open the output file.
        ew: KgtkWriter = KgtkWriter.open(column_names,
                                         self.output_file_path,
                                         mode=kr.mode,
                                         require_all_columns=False,
                                         prohibit_extra_columns=True,
                                         fill_missing_columns=True,
                                         gzip_in_parallel=False,
                                         verbose=self.verbose,
                                         very_verbose=self.very_verbose)        
        
        if self.verbose:
            print("Expanding records from %s" % self.input_file_path, file=self.error_file, flush=True)
        input_line_count: int = 0
        output_line_count: int = 0;

        row: typing.List[str]
        for row in kr:
            input_line_count += 1

            # Parse the value for the colummn being exploded:
            item_to_explode: str = row[column_idx]
            value: KgtkValue = KgtkValue(item_to_explode, parse_fields=True)
            value.validate()
            if not value.is_valid():
                if self.verbose:
                    print("Not exploding invalid item '%s' in input line %d" % (item_to_explode, input_line_count), file=self.error_file, flush=True)
                ew.write(row) # This will be filled to the proper length
                output_line_count += 1
                continue

            if value.is_list():
                if self.verbose:
                    print("Exploding a list: '%s'" % item_to_explode, file=self.error_file, flush=True)
                subvalue: KgtkValue
                for subvalue in value.get_list_items():
                    if self.very_verbose:
                        print("Exploding '%s'" % subvalue.value)
                    ew.write(self.explode(subvalue, row, explosion, new_column_count))
                    output_line_count += 1
            else:
                if self.very_verbose:
                    print("Exploding '%s'" % value.value)
                ew.write(self.explode(value, row, explosion, new_column_count))
                output_line_count += 1

        if self.verbose:
            print("Read %d records, wrote %d records." % (input_line_count, output_line_count), file=self.error_file, flush=True)
        
        ew.close()

    def explosion(self, value: KgtkValue, row: typing.List[str], explosion: typing.Mapping[str, int], new_column_count: int)->typing.List[str]:
        newrow: typing.List[str] = row.copy()
        if new_column_count > 0:
            # Would it be better to do:
            #
            # if new_column_count > 0:
            #     newrow.extend(["] * new_column_count)
            i: int
            for i in range(new_column_count):
                newrow.append("")
        field_map: typing.Mapping[str, typing.Union[str, int, float, bool]] = value.get_field_map()
        field_name: str
        idx: int
        for field_name, idx in explosion.items():
            if field_name in field_map:
                newrow[idx] = repr(field_map[field_name])
        return newrow
            

def main():
    """
    Test the KGTK ifempty processor.
    """
    parser: ArgumentParser = ArgumentParser()

    parser.add_argument(dest="input_file_path", help="The KGTK file with the input data. (default=%(default)s)", type=Path, nargs="?", default="-")

    parser.add_argument(      "--column", dest="column_name", help="The name of the column to explode. (default=%(default)s).", default="node2")

    parser.add_argument(      "--fields", dest="field_names", help="The names of the field to extract. (default=%(default)s).", nargs='+',
                              default=KgtkValueFields.FIELD_NAMES, choices=KgtkValueFields.FIELD_NAMES)

    parser.add_argument("-o", "--output-file", dest="output_file_path", help="The KGTK file to write (default=%(default)s).", type=Path, default="-")
    
    parser.add_argument(      "--prefix", dest="prefix", help="The prefix for exploded column names. (default=%(default)s).", default="node2;")

    parser.add_argument(      "--overwrite", dest="overwrite_columns",
                              help="Indicate that it is OK to overwrite existing columns. (default=%(default)s).",
                              type=optional_bool, nargs='?', const=True, default=False)

    parser.add_argument(      "--expand", dest="expand_list",
                              help="Expand the source column if it contains a list, else fail. (default=%(default)s).",
                              type=optional_bool, nargs='?', const=True, default=False)

    KgtkReader.add_debug_arguments(parser)
    KgtkReaderOptions.add_arguments(parser, mode_options=True)
    KgtkValueOptions.add_arguments(parser)

    args: Namespace = parser.parse_args()

    error_file: typing.TextIO = sys.stdout if args.errors_to_stdout else sys.stderr

    # Build the option structures.                                                                                                                          
    reader_options: KgtkReaderOptions = KgtkReaderOptions.from_args(args)
    value_options: KgtkValueOptions = KgtkValueOptions.from_args(args)

   # Show the final option structures for debugging and documentation.                                                                                             
    if args.show_options:
        # TODO: show ifempty-specific options.
        print("input: %s" % str(args.input_file_path), file=error_file, flush=True)
        print("--column %s" % args.column_name, file=error_file, flush=True)
        print("--prefix %s" % args.prefix, file=error_file, flush=True)
        print("--overwrite %s" % str(args.overwrite), file=error_file, flush=True)
        print("--expand %s" % str(args.expand_list), file=error_file, flush=True)
        if args.fields is not None:
            print("--fields %s" % " ".join(args.field_names), file=error_file, flush=True)
        print("--output-file=%s" % str(args.output_file_path))
        reader_options.show(out=error_file)
        value_options.show(out=error_file)

    ex: KgtkExplode = KgtkExplode(
        input_file_path=args.input_file_path,
        column_name=args.column_name,
        prefix=args.prefix,
        field_names=args.field_names,
        overwrite_columns=args.overwrite_columns,
        expand_list=args.expand_list,
        output_file_path=args.output_file_path,
        reader_options=reader_options,
        value_options=value_options,
        error_file=error_file,
        verbose=args.verbose,
        very_verbose=args.very_verbose)

    ex.process()

if __name__ == "__main__":
    main()
