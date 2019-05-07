"""Implementations of TableWriter and -Reader for HDF5 files"""
import enum
from functools import partial

import numpy as np
import tables
from astropy.time import Time
from astropy.units import Quantity

import ctapipe
from .tableio import TableWriter, TableReader
from ..core import Container

__all__ = ["HDF5TableWriter", "HDF5TableReader"]

PYTABLES_TYPE_MAP = {
    "float": tables.Float64Col,
    "float64": tables.Float64Col,
    "float32": tables.Float32Col,
    "int": tables.IntCol,
    "int16": tables.Int16Col,
    "int32": tables.Int32Col,
    "int64": tables.Int64Col,
    "uint16": tables.UInt16Col,
    "uint32": tables.UInt32Col,
    "uint64": tables.UInt64Col,
    "bool": tables.UInt8Col,
}


class HDF5TableWriter(TableWriter):
    """
    A very basic table writer that can take a container (or more than one)
    and write it to an HDF5 file. It does _not_ recursively write the
    container. This is intended as a building block to create a more complex
    I/O system.

    It works by creating a HDF5 Table description from the `Field`s inside a
    container, where each item becomes a column in the table. The first time
    `HDF5TableWriter.write()` is called, the container(s) are registered
    and the table created in the output file.

    Each item in the container can also have an optional transform function
    that is called before writing to transform the value.  For example,
    unit quantities always have their units removed, or converted to a
    common unit if specified in the `Field`.

    Any metadata in the `Container` (stored in `Container.meta`) will be
    written to the table's header on the first call to write()

    Multiple tables may be written at once in a single file, as long as you
    change the table_name attribute to `write()` to specify which one to write
    to.  Likewise multiple Containers can be merged into a single output
    table by passing a list of containers to `write()`.

    To append to existing files, pass the `mode='a'`  option to the
    constructor.

    Parameters
    ----------
    filename: str
        name of hdf5 output file
    group_name: str
        name of group into which to put all of the tables generated by this
        Writer (it will be placed under "/" in the file)
    add_prefix: bool
        if True, add the container prefix before each column name
    mode : str ('w', 'a')
        'w' if you want to overwrite the file
        'a' if you want to append data to the file
    root_uep : str
        root location of the `group_name`
    kwargs:
        any other arguments that will be passed through to `pytables.open()`.
        e.g. to set the compression level to 7 pass : `filters=tables.Filters(
        complevel=7)`

    """

    def __init__(
        self, filename, group_name, add_prefix=False, mode="w", root_uep="/", **kwargs
    ):

        super().__init__(add_prefix=add_prefix)
        self._schemas = {}
        self._tables = {}

        if mode not in ["a", "w", "r+"]:
            raise IOError(f"The mode {mode} is not supported for writing")

        kwargs.update(mode=mode, root_uep=root_uep)

        self.open(filename, **kwargs)

        if root_uep + group_name in self._h5file:

            self._group = self._h5file.get_node(root_uep + group_name)

        else:

            self._group = self._h5file.create_group(root_uep, group_name)

        self.log.debug("h5file: %s", self._h5file)

    def open(self, filename, **kwargs):

        self.log.debug("kwargs for tables.open_file: %s", kwargs)
        self._h5file = tables.open_file(filename, **kwargs)

    def close(self):

        self._h5file.close()

    def _create_hdf5_table_schema(self, table_name, containers):
        """
        Creates a pytables description class for the given containers
        and registers it in the Writer

        Parameters
        ----------
        table_name: str
            name of table
        container: ctapipe.core.Container
            instance of an initalized container

        Returns
        -------
        dictionary of extra metadata to add to the table's header
        """

        class Schema(tables.IsDescription):
            pass

        meta = {}  # any extra meta-data generated here (like units, etc)

        # create pytables schema description for the given container
        for container in containers:
            for col_name, value in container.items(add_prefix=self.add_prefix):

                typename = ""
                shape = 1

                if self._is_column_excluded(table_name, col_name):
                    self.log.debug("excluded column: %s/%s", table_name, col_name)
                    continue

                # apply any user-defined transforms first
                value = self._apply_col_transform(table_name, col_name, value)

                if isinstance(value, enum.Enum):

                    def transform(enum_value):
                        """transform enum instance into its (integer) value"""
                        return enum_value.value

                    meta[f"{col_name}_ENUM"] = value.__class__
                    value = transform(value)
                    self.add_column_transform(table_name, col_name, transform)

                if isinstance(value, Quantity):
                    if self.add_prefix and container.prefix:
                        key = col_name.replace(container.prefix + "_", "")
                    else:
                        key = col_name
                    req_unit = container.fields[key].unit

                    if req_unit is not None:
                        tr = partial(tr_convert_and_strip_unit, unit=req_unit)
                        meta[f"{col_name}_UNIT"] = str(req_unit)
                    else:
                        tr = lambda x: x.value
                        meta[f"{col_name}_UNIT"] = str(value.unit)

                    value = tr(value)
                    self.add_column_transform(table_name, col_name, tr)

                if isinstance(value, np.ndarray):
                    typename = value.dtype.name
                    coltype = PYTABLES_TYPE_MAP[typename]
                    shape = value.shape
                    Schema.columns[col_name] = coltype(shape=shape)

                if isinstance(value, Time):
                    # TODO: really should use MET, but need a func for that
                    Schema.columns[col_name] = tables.Float64Col()
                    self.add_column_transform(table_name, col_name, tr_time_to_float)

                elif type(value).__name__ in PYTABLES_TYPE_MAP:
                    typename = type(value).__name__
                    coltype = PYTABLES_TYPE_MAP[typename]
                    Schema.columns[col_name] = coltype()

                self.log.debug(
                    "Table %s: added col: %s type: %s shape: %s",
                    table_name,
                    col_name,
                    typename,
                    shape,
                )

        self._schemas[table_name] = Schema
        meta["CTAPIPE_VERSION"] = ctapipe.__version__
        return meta

    def _setup_new_table(self, table_name, containers):
        """ set up the table. This is called the first time `write()`
        is called on a new table """
        self.log.debug("Initializing table '%s'", table_name)
        meta = self._create_hdf5_table_schema(table_name, containers)

        for container in containers:
            meta.update(container.meta)  # copy metadata from container

        table = self._h5file.create_table(
            where=self._group,
            name=table_name,
            title="Storage of {}".format(
                ",".join(c.__class__.__name__ for c in containers)
            ),
            description=self._schemas[table_name],
        )
        for key, val in meta.items():
            table.attrs[key] = val

        self._tables[table_name] = table

    def _append_row(self, table_name, containers):
        """
        append a row to an already initialized table. This is called
        automatically by `write()`
        """
        table = self._tables[table_name]
        row = table.row

        for container in containers:
            selected_fields = filter(
                lambda kv: kv[0] in table.colnames,
                container.items(add_prefix=self.add_prefix),
            )
            for colname, value in selected_fields:

                value = self._apply_col_transform(table_name, colname, value)

                row[colname] = value
        row.append()

    def write(self, table_name, containers):
        """
        Write the contents of the given container or containers to a table.
        The first call to write  will create a schema and initialize the table
        within the file.
        The shape of data within the container must not change between
        calls, since variable-length arrays are not supported.

        Parameters
        ----------
        table_name: str
            name of table to write to
        containers: `ctapipe.core.Container` or `Iterable[ctapipe.core.Container]`
            container to write
        """
        if isinstance(containers, Container):
            containers = (containers,)

        if table_name not in self._schemas:
            self._setup_new_table(table_name, containers)

        self._append_row(table_name, containers)


class HDF5TableReader(TableReader):
    """
    Reader that reads a single row of an HDF5 table at once into a Container.
    Simply construct a `HDF5TableReader` with an input HDF5 file,
    and call the `read(path, container)` method to get a generator that fills
    the given container with a new row of the table on each access.

    Columns in the table are automatically mapped to container fields by
    name, and if a field is missing in either, it is skipped during read,
    but a warning is emitted.

    Columns that were written by HDF5TableWriter and which had unit
    transforms applied, will have the units re-applied when reading (the
    unit used is stored in the header attributes).

    Note that this is only useful if you want to read all information *one
    event at a time* into a container, which is not very I/O efficient. For
    some other use cases, it may be much more efficient to access the
    table data directly, for example to read an entire column or table at
    once (which means not using the Container data structure).

    Todo:
    - add ability to synchronize reading of multiple tables on a key

    - add ability (also with TableWriter) to read a row into n containers at
        once, assuming no naming conflicts (so we can add e.g. event_id)

    """

    def __init__(self, filename, **kwargs):
        """
        Parameters
        ----------
        filename: str
            name of hdf5 file
        kwargs:
            any other arguments that will be passed through to
            `pytables.open()`.
        """

        super().__init__()
        self._tables = {}
        kwargs.update(mode="r")

        self.open(filename, **kwargs)

    def open(self, filename, **kwargs):

        self._h5file = tables.open_file(filename, **kwargs)

    def close(self):

        self._h5file.close()

    def _setup_table(self, table_name, container):
        tab = self._h5file.get_node(table_name)
        self._tables[table_name] = tab
        self._map_table_to_container(table_name, container)
        self._map_transforms_from_table_header(table_name)
        return tab

    def _map_transforms_from_table_header(self, table_name):
        """
        create any transforms needed to "undo" ones in the writer
        """
        tab = self._tables[table_name]
        for attr in tab.attrs._f_list():
            if attr.endswith("_UNIT"):
                colname = attr[:-5]
                tr = partial(tr_add_unit, unitname=tab.attrs[attr])
                self.add_column_transform(table_name, colname, tr)

        for attr in tab.attrs._f_list():
            if attr.endswith("_ENUM"):
                colname = attr[:-5]

                def transform_int_to_enum(int_val):
                    """transform integer 'code' into enum instance"""
                    enum_class = tab.attrs[attr]
                    return enum_class(int_val)

                self.add_column_transform(table_name, colname, transform_int_to_enum)

    def _map_table_to_container(self, table_name, container):
        """ identifies which columns in the table to read into the container,
        by comparing their names."""
        tab = self._tables[table_name]
        for colname in tab.colnames:
            if colname in container.fields:
                self._cols_to_read[table_name].append(colname)
            else:
                self.log.warning(
                    "Table '%s' has column '%s' that is not in "
                    "container %s. It will be skipped",
                    table_name,
                    colname,
                    container.__class__.__name__,
                )

        # also check that the container doesn't have fields that are not
        # in the table:
        for colname in container.fields:
            if colname not in self._cols_to_read[table_name]:
                self.log.warning(
                    "Table '%s' is missing column '%s' that is "
                    "in container %s. It will be skipped.",
                    table_name,
                    colname,
                    container.__class__.__name__,
                )

        # copy all user-defined attributes back to Container.mets
        for key in tab.attrs._f_list():
            container.meta[key] = tab.attrs[key]

    def read(self, table_name: str, container: Container):
        """
        Returns a generator that reads the next row from the table into the
        given container.  The generator returns the same container. Note that
        no containers are copied, the data are overwritten inside.

        Parameters
        ----------
        table_name: str
            name of table to read from
        container : ctapipe.core.Container
            Container instance to fill
        """
        if table_name not in self._tables:
            tab = self._setup_table(table_name, container)
        else:
            tab = self._tables[table_name]

        row_count = 0

        while 1:

            try:
                row = tab[row_count]
            except IndexError:
                return  # stop generator when done

            for colname in self._cols_to_read[table_name]:
                container[colname] = self._apply_col_transform(
                    table_name, colname, row[colname]
                )

            yield container
            row_count += 1


def tr_convert_and_strip_unit(quantity, unit):
    return quantity.to(unit).value


def tr_list_to_mask(thelist, length):
    """ transform list to a fixed-length mask"""
    arr = np.zeros(shape=length, dtype=np.bool)
    arr[thelist] = True
    return arr


def tr_time_to_float(thetime):
    return thetime.mjd


def tr_add_unit(value, unitname):
    return Quantity(value, unitname)
