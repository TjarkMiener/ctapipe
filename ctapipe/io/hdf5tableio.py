"""Implementations of TableWriter and -Reader for HDF5 files"""
import enum
from pathlib import PurePath

import numpy as np
import tables
from astropy.time import Time
from astropy.units import Quantity, Unit

import ctapipe

from ..core import Container, Map
from .tableio import (
    EnumColumnTransform,
    FixedPointColumnTransform,
    QuantityColumnTransform,
    StringTransform,
    TableReader,
    TableWriter,
    TimeColumnTransform,
)

__all__ = ["HDF5TableWriter", "HDF5TableReader"]

PYTABLES_TYPE_MAP = {
    "float": tables.Float64Col,
    "float64": tables.Float64Col,
    "float32": tables.Float32Col,
    "float16": tables.Float16Col,
    "int8": tables.Int8Col,
    "int16": tables.Int16Col,
    "int32": tables.Int32Col,
    "int64": tables.Int64Col,
    "int": tables.Int64Col,
    "uint8": tables.UInt8Col,
    "uint16": tables.UInt16Col,
    "uint32": tables.UInt32Col,
    "uint64": tables.UInt64Col,
    "bool": tables.BoolCol,  # python bool
    "bool_": tables.BoolCol,  # np.bool_
}


DEFAULT_FILTERS = tables.Filters(
    complevel=5,  # compression medium, tradeoff between speed and compression
    complib="blosc:zstd",  # use modern zstd algorithm
    fletcher32=True,  # add checksums to data chunks
)


ALLOWED_SUFFIXES = {
    "_DESC",
    "_ENUM",
    "_TIME_FORMAT",
    "_TIME_SCALE",
    "_TRANSFORM",
    "_TRANSFORM_SCALE",
    "_UNIT",
}


def get_hdf5_attr(attrs, name, default=None):
    if name in attrs:
        return attrs[name]
    return default


class HDF5TableWriter(TableWriter):
    """
    A very basic table writer that can take a container (or more than one)
    and write it to an HDF5 file. It does _not_ recursively write the
    container. This is intended as a building block to create a more complex
    I/O system.

    It works by creating a HDF5 Table description from the `~ctapipe.core.Field`
    definitions inside a container, where each item becomes a column in the table.
    The first time `HDF5TableWriter.write()` is called, the container(s) are
    registered and the table created in the output file.

    Each item in the container can also have an optional transform function
    that is called before writing to transform the value.  For example,
    unit quantities always have their units removed, or converted to a
    common unit if specified in the `~ctapipe.core.Field`.

    Any metadata in the `~ctapipe.core.Container` (stored in ``Container.meta``) will be
    written to the table's header on the first call to write()

    Multiple tables may be written at once in a single file, as long as you
    change the table_name attribute to `write()` to specify which one to write
    to.  Likewise multiple Containers can be merged into a single output
    table by passing a list of containers to `write()`.

    To append to existing files, pass the ``mode='a'``  option to the
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
        root location of the ``group_name``
    filters: pytables.Filters
        A set of filters (compression settings) to be used for
        all datasets created by this writer.
    kwargs:
        any other arguments that will be passed through to ``pytables.open_file``.
    """

    def __init__(
        self,
        filename,
        group_name="",
        add_prefix=False,
        mode="w",
        root_uep="/",
        filters=DEFAULT_FILTERS,
        parent=None,
        config=None,
        **kwargs,
    ):

        super().__init__(add_prefix=add_prefix, parent=parent, config=config)
        self._schemas = {}
        self._tables = {}

        if mode not in ["a", "w", "r+"]:
            raise IOError(f"The mode '{mode}' is not supported for writing")

        kwargs.update(mode=mode, root_uep=root_uep, filters=filters)

        self.open(str(filename), **kwargs)
        self._group = "/" + group_name
        self.filters = filters

        self.log.debug("h5file: %s", self.h5file)

    def open(self, filename, **kwargs):
        self.log.debug("kwargs for tables.open_file: %s", kwargs)
        self.h5file = tables.open_file(filename, **kwargs)

    def close(self):
        self.h5file.close()

    def _add_column_to_schema(self, table_name, schema, meta, pos, field, name, value):
        typename = ""
        shape = 1

        if isinstance(value, Container):
            self.log.debug("Ignoring sub-container: %s/%s", table_name, name)
            return

        if isinstance(value, Map):
            self.log.debug("Ignoring map-field: %s/%s", table_name, name)
            return

        if self._is_column_excluded(table_name, name):
            self.log.debug("excluded column: %s/%s", table_name, name)
            return

        if name in schema.columns:
            self.log.warning("Found duplicated column %s, skipping", name)
            return

        # apply any user-defined transforms first
        value = self._apply_col_transform(table_name, name, value)

        # now set up automatic transforms to make values that cannot be
        # written in their default form into a form that is serializable
        if isinstance(value, enum.Enum):
            tr = EnumColumnTransform(enum=value.__class__)
            value = tr(value)
            self.add_column_transform(table_name, name, tr)

        if isinstance(value, Quantity):
            unit = field.unit or value.unit
            tr = QuantityColumnTransform(unit=unit)
            value = tr(value)
            self.add_column_transform(table_name, name, tr)

        if isinstance(value, np.ndarray):
            typename = value.dtype.name
            coltype = PYTABLES_TYPE_MAP[typename]
            shape = value.shape
            schema.columns[name] = coltype(shape=shape, pos=pos)

        elif isinstance(value, Time):
            # TODO: really should use MET, but need a func for that
            schema.columns[name] = tables.Float64Col(pos=pos)
            tr = TimeColumnTransform(scale="tai", format="mjd")
            self.add_column_transform(table_name, name, tr)

        elif type(value).__name__ in PYTABLES_TYPE_MAP:
            typename = type(value).__name__
            coltype = PYTABLES_TYPE_MAP[typename]
            schema.columns[name] = coltype(pos=pos)

        elif isinstance(value, str):
            max_length = field.max_length or len(value.encode("utf-8"))
            tr = StringTransform(max_length)
            self.add_column_transform(table_name, name, tr)
            schema.columns[name] = tables.StringCol(itemsize=max_length)

        else:
            raise ValueError(f"Column {name} of type {type(value)} not writable")

        # add meta fields of transform
        transform = self._transforms[table_name].get(name)
        if transform is not None:
            if hasattr(transform, "get_meta"):
                meta.update(transform.get_meta(name))

        # add desription to metadata
        meta[f"{name}_DESC"] = field.description

        self.log.debug(
            f"Table {table_name}: "
            f"added col: {name} type: "
            f"{typename} shape: {shape} "
            f"with transform: {transform} "
        )

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

        # set up any column tranforms that were requested as regexps (i.e.
        # convert them to explicit transform in the _transforms dict if they
        # match)
        self._realize_regexp_transforms(table_name, containers)

        # create pytables schema description for the given container
        pos = 0
        for container in containers:

            container.validate()  # ensure the data are complete

            it = zip(
                container.items(add_prefix=self.add_prefix), container.fields.values()
            )
            for (col_name, value), field in it:
                try:
                    self._add_column_to_schema(
                        table_name=table_name,
                        schema=Schema,
                        meta=meta,
                        pos=pos,
                        field=field,
                        name=col_name,
                        value=value,
                    )
                    pos += 1
                except ValueError:
                    self.log.warning(
                        f"Column {col_name}"
                        f" with value {value!r} of type {type(value)} "
                        f" of container {container.__class__.__name__} in"
                        f" table {table_name} not writable, skipping"
                    )
        self._schemas[table_name] = Schema
        meta["CTAPIPE_VERSION"] = ctapipe.__version__
        return meta

    def _setup_new_table(self, table_name, containers):
        """set up the table. This is called the first time `write()`
        is called on a new table"""
        self.log.debug("Initializing table '%s' in group '%s'", table_name, self._group)
        meta = self._create_hdf5_table_schema(table_name, containers)

        if table_name.startswith("/"):
            raise ValueError("Table name must not start with '/'")

        table_path = PurePath(self._group) / PurePath(table_name)
        table_group = str(table_path.parent)
        table_basename = table_path.stem
        table_path = str(table_path)

        for container in containers:
            meta.update(container.meta)  # copy metadata from container

        if table_path not in self.h5file:
            table = self.h5file.create_table(
                where=table_group,
                name=table_basename,
                title="Storage of {}".format(
                    ",".join(c.__class__.__name__ for c in containers)
                ),
                description=self._schemas[table_name],
                createparents=True,
                filters=self.filters,
            )
            self.log.debug(f"CREATED TABLE: {table}")
            for key, val in meta.items():
                table.attrs[key] = val
        else:
            table = self.h5file.get_node(table_path)

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
                try:
                    value = self._apply_col_transform(table_name, colname, value)
                    row[colname] = value
                except Exception:
                    self.log.error(
                        f"Error writing col {colname} of "
                        f"container {container.__class__.__name__}"
                    )
                    raise
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
    and call the `read(path, container) <read>`_ method to get a generator that fills
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
    """

    def __init__(self, filename, **kwargs):
        """
        Parameters
        ----------
        filename: str, pathlib.PurePath or tables.File instance
            name of hdf5 file or file handle
        kwargs:
            any other arguments that will be passed through to
            `pytables.file.open_file`.
        """

        super().__init__()
        self._tables = {}
        self._cols_to_read = {}
        self._missing_cols = {}
        self._meta = {}
        kwargs.update(mode="r")

        if isinstance(filename, str) or isinstance(filename, PurePath):
            self.open(filename, **kwargs)
        elif isinstance(filename, tables.File):
            self._h5file = filename
        else:
            raise NotImplementedError(
                "filename needs to be either a string, pathlib.PurePath "
                "or tables.File"
            )

    def open(self, filename, **kwargs):

        self._h5file = tables.open_file(filename, **kwargs)

    def close(self):

        self._h5file.close()

    def _setup_table(self, table_name, containers, prefixes, ignore_columns):
        tab = self._h5file.get_node(table_name)
        self._tables[table_name] = tab
        self._map_table_to_containers(table_name, containers, prefixes, ignore_columns)
        self._handle_metadata(table_name, containers, prefixes, ignore_columns)
        self._map_transforms_from_table_header(table_name)
        return tab

    def _map_transforms_from_table_header(self, table_name):
        """
        create any transforms needed to "undo" ones in the writer
        """
        tab = self._tables[table_name]
        attrs = tab.attrs._f_list()
        for attr in attrs:
            if attr.endswith("_UNIT"):
                colname = attr[:-5]
                tr = QuantityColumnTransform(unit=Unit(tab.attrs[attr]))
                self.add_column_transform(table_name, colname, tr)

            elif attr.endswith("_ENUM"):
                colname = attr[:-5]
                tr = EnumColumnTransform(tab.attrs[attr])
                self.add_column_transform(table_name, colname, tr)

            elif attr.endswith("_TIME_SCALE"):
                colname, _, _ = attr.rpartition("_TIME_SCALE")
                scale = tab.attrs[attr]
                time_format = get_hdf5_attr(tab.attrs, colname + "_TIME_FORMAT", "mjd")
                transform = TimeColumnTransform(scale=scale, format=time_format)
                self.add_column_transform(table_name, colname, transform)

            elif attr.endswith("_TRANSFORM_SCALE"):
                colname, _, _ = attr.rpartition("_TRANSFORM_SCALE")
                tr = FixedPointColumnTransform(
                    scale=tab.attrs[attr],
                    offset=get_hdf5_attr(tab.attrs, colname + "_TRANSFORM_OFFSET", 0),
                    source_dtype=get_hdf5_attr(
                        tab.attrs, colname + "_TRANSFORM_DTYPE", "float32"
                    ),
                    target_dtype=tab.dtype[colname].base,
                )
                self.add_column_transform(table_name, colname, tr)
            elif attr.endswith("_TRANSFORM"):
                colname, _, _ = attr.rpartition("_TRANSFORM")
                if tab.attrs[attr] == "string":
                    maxlen = tab.attrs[f"{colname}_MAXLEN"]
                    tr = StringTransform(maxlen)
                    self.add_column_transform(table_name, colname, tr)

    def _handle_metadata(self, table_name, containers, prefixes, ignore_columns):
        tab = self._tables[table_name]
        self._meta[table_name] = {}
        tab_meta_names = tab.attrs._f_list()
        used_keys = []

        def _is_allowed(key, container, prefix):
            if prefix:
                key = f"{prefix}_{key}"
            for suffix in ALLOWED_SUFFIXES:
                _key, *_ = key.split(suffix)
                if _key in container.fields:
                    return True
            return False

        for container, prefix in zip(containers, prefixes):
            container_name = container.__name__
            self._meta[table_name][container_name] = {}
            for key in tab_meta_names:
                if _is_allowed(key, container, prefix):
                    self._meta[table_name][container_name][key] = tab.attrs[key]
                    used_keys.append(key)

        for key in tab_meta_names:
            if key in used_keys:
                continue
            for container in containers:
                self._meta[table_name][container.__name__][key] = tab.attrs[key]

    def _map_table_to_containers(
        self, table_name, containers, prefixes, ignore_columns
    ):
        """identifies which columns in the table to read into the containers,
        by comparing their names including an optional prefix."""
        tab = self._tables[table_name]
        self._cols_to_read[table_name] = []
        self._missing_cols[table_name] = []
        for container, prefix in zip(containers, prefixes):
            self._missing_cols[table_name].append([])

            for colname in tab.colnames:
                if prefix and colname.startswith(prefix):
                    colname_without_prefix = colname[len(prefix) + 1 :]
                else:
                    colname_without_prefix = colname
                if colname_without_prefix in container.fields:
                    self._cols_to_read[table_name].append(colname)
                else:
                    self.log.debug(
                        f"Table {table_name} has column {colname_without_prefix} that is not in "
                        f"container {container.__class__.__name__}. It will be skipped."
                    )

            # also check that the container doesn't have fields that are not
            # in the table:
            for colname in container.fields:
                if colname in ignore_columns:
                    continue

                if prefix:
                    colname_with_prefix = f"{prefix}_{colname}"
                else:
                    colname_with_prefix = colname

                if colname_with_prefix not in self._cols_to_read[table_name]:
                    self._missing_cols[table_name][-1].append(colname)
                    self.log.warning(
                        f"Table {table_name} is missing column {colname_with_prefix} "
                        f"that is in container {container}. "
                        "It will be skipped."
                    )

        # check if the table has additional columns not present in any container
        for colname in tab.colnames:
            if colname not in self._cols_to_read[table_name]:
                self.log.debug(
                    f"Table {table_name} contains column {colname} "
                    "that does not map to any of the specified containers"
                )

    def read(self, table_name, containers, prefixes=False, ignore_columns=None):
        """
        Returns a generator that reads the next row from the table into the
        given container. The generator returns the same container. Note that
        no containers are copied, the data are overwritten inside.

        Parameters
        ----------
        table_name: str
            name of table to read from
        containers : Iterable[ctapipe.core.Container]
            Container classes to fill
        prefix: bool, str or list
            Prefix that was added while writing the file.
            If True, the container prefix is taken into consideration, when
            comparing column names and container fields.
            If False, no prefix is used.
            If a string is provided, it is used as prefix for all containers.
            If a list is provided, the length needs to match th number
            of containers.
        """

        ignore_columns = set(ignore_columns) if ignore_columns is not None else set()

        return_iterable = True

        if isinstance(containers, Container):
            raise TypeError("Expected container *classes*, not *instances*")

        # check for a single container
        if isinstance(containers, type):
            containers = (containers,)
            return_iterable = False

        for container in containers:
            if isinstance(container, Container):
                raise TypeError("Expected container *classes*, not *instances*")

        if prefixes is False:
            prefixes = ["" for _ in containers]
        elif prefixes is True:
            prefixes = [container.default_prefix for container in containers]
        elif isinstance(prefixes, str):
            prefixes = [prefixes for _ in containers]

        if len(prefixes) != len(containers):
            raise ValueError("Length of provided prefixes does not match containers")

        if table_name not in self._tables:
            tab = self._setup_table(table_name, containers, prefixes, ignore_columns)
        else:
            tab = self._tables[table_name]

        missing = self._missing_cols[table_name]

        for row_index in range(len(tab)):
            # looping over table yields Row instances.
            # __getitem__ just gives plain numpy data
            row = tab[row_index]

            ret = []
            for cls, prefix, missing_cols in zip(containers, prefixes, missing):
                kwargs = {}
                for fieldname in cls.fields.keys():

                    if prefix:
                        colname = f"{prefix}_{fieldname}"
                    else:
                        colname = fieldname

                    if colname not in self._cols_to_read[table_name]:
                        continue

                    kwargs[fieldname] = self._apply_col_transform(
                        table_name, colname, row[colname]
                    )

                # set missing fields to None
                for fieldname in missing_cols:
                    kwargs[fieldname] = None

                container = cls(**kwargs, prefix=prefix)
                container.meta = self._meta[table_name][container.__class__.__name__]
                ret.append(container)

            if return_iterable:
                yield ret
            else:
                yield ret[0]
