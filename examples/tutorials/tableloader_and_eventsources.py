"""
IO: TableLoader and EventSources
================================

This hands-on was presented in October 2023 at the DPPS Meeting
at CEA Paris-Saclay (J. Hackfeld).
"""

# %%
# Introduction
# ------------
# ``ctapipe`` provides basically two different ways of accessing its data products:
#
# - event-wise (**EventSource**)
# - column-wise (**TableLoader**)
#
# **EventSource(s):**
#
# EventSources read input files and generate ``ctapipe.containers.ArrayEventContainer``
# instances when iterated over.
#
# A new EventSource should be created for each type of event file read
# into ctapipe, e.g. simtel files are read by the ``ctapipe.io.SimTelEventSource`` .
#
# EventSource provides a common high-level interface for accessing event
# information from different data sources. Creating an EventSource for a new
# file format or other event source ensures that data can be accessed in a common way,
# regardless of the file format or data origin.
#
# EventSource itself is an abstract class, but will create an
# appropriate subclass if a compatible source is found for the given
# ``input_url`` .
#
# **TableLoader**:
#
# Loads telescope-event or subarray-event data from ctapipe HDF5 files and returns
# ``astropy.table`` .
# See `Astropy docs <https://docs.astropy.org/en/stable/table>`__
# or `this video from A.Donath at the ESCAPE Summer School
# 2021 <https://www.youtube.com/watch?v=uzhQ6RIGHQA>`__.
#
# This class provides high-level access to data stored in ctapipe HDF5 files,
# such as created by the ctapipe-process tool ( ``ctapipe.tools.process.ProcessorTool`` ).
#
# There are multiple ``TableLoader`` methods loading data from all relevant tables
# (depending on the options) and **joins** them into single tables:
#
# -  ``TableLoader.read_subarray_events``
# -  ``TableLoader.read_telescope_events``
# -  ``TableLoader.read_telescope_events_by_id``
# -  ``TableLoader.read_telescope_events_by_type``
#
# The last one returns a dict with a table per telescope type, which is needed for
# e.g. DL1 image data that might have different shapes for each of the telescope
# types as tables do not support variable length columns.
#
# **It is recommended to use the** ``TableLoader`` **when loading data into Tables,
# because its much faster than EventSources!**

# %%
# Code Examples
# -------------
# First import some classes/modules and get the example dataset path:

from traitlets.config import Config

from ctapipe import utils
from ctapipe.calib import CameraCalibrator
from ctapipe.io import DataWriter, EventSource, TableLoader

simtel_path = utils.get_dataset_path("gamma_prod5.simtel.zst")

# %%
# EventSource(s)
# --------------
# The already implemented EventSources are:

sources = EventSource.non_abstract_subclasses()
maxlen = max(len(source) for source in sources)
for source in sources.values():
    print(f"{source.__name__:>{maxlen}s} -- {source.__module__}.{source.__qualname__}")

# %%
# ``EventSource`` will create an appropriate subclass if a compatible source is found for
# the given ``input_url`` :

source = EventSource(input_url=simtel_path, max_events=5)
print(source)

# %%
# You can now loop over the ``ctapipe.containers.ArrayEventContainer`` generated by the
# source.

for event in source:
    print(event.count)

# %%

print(repr(event))

# %%
# Every time a new loop is started through the source,
# it tries to restart from the first event, which might not be supported
# by the event source. It is encouraged to use ``EventSource`` in a **context manager**
# to ensure the correct cleanups are performed when you are finished with the source:

with EventSource(input_url=simtel_path) as source:
    for event in source:
        print(
            f"Event Count: {event.count},"
            f"Tels with trigger: {event.trigger.tels_with_trigger}"
        )

# %%
# You can hand in the ID's of the telescopes to be included in the data
# with the ``allowed_tels`` attribute. If given, only this subset of telescopes
# will be present in the generated events. If None, all available telescopes are used.

eventsource_config = Config(
    {"EventSource": {"max_events": 5, "allowed_tels": [3, 4, 9]}}
)

with EventSource(input_url=simtel_path, config=eventsource_config) as source:
    for event in source:
        print(
            f"Event Count: {event.count},"
            f"Tels with trigger: {event.trigger.tels_with_trigger}"
        )

# %%
# If you want to calibrate your data in the event loop and write it to an .h5 file with
# the ``DataWriter`` :
#

source = EventSource(input_url=simtel_path, max_events=50)
calibrate = CameraCalibrator(subarray=source.subarray)

with DataWriter(
    event_source=source,
    output_path="events.dl1.h5",
    write_dl1_parameters=False,
    overwrite=True,
    write_dl1_images=True,
) as write_data:
    for event in source:
        calibrate(event)
        write_data(event)

# %%
# Alternatively doing it with ``ctapipe-process`` would look like this:
#
# .. code-block:: bash
#
#   ! ctapipe-process -i {simtel_path} -o events.dl1.h5 --overwrite --progress
#

# %%
# TableLoader
# -----------
#
# Create a TableLoader instance with the above created dl1 file:

loader = TableLoader(input_url="events.dl1.h5")

# %%
# Alternatively using a config file:

tableloader_config = Config(
    {
        "TableLoader": {
            "input_url": "events.dl1.h5",
        }
    }
)

loader = TableLoader(config=tableloader_config)

# %%
# Reading subarray-based event information:

subarray_events = loader.read_subarray_events(
    start=None,
    stop=None,
    dl2=False,
    simulated=True,
    observation_info=False,
)

subarray_events

# %%
# Reading subarray-based event information in chunks:

subarray_events_chunked = loader.read_subarray_events_chunked(
    chunk_size=3,
    dl2=False,
    simulated=True,
    observation_info=False,
)

for chunk in subarray_events_chunked:
    print(" \n", chunk)

# %%
# Reading just LST events:

lst_events = loader.read_telescope_events(
    telescopes=[1, 2, 3, 4],
    start=None,
    stop=None,
    dl1_images=True,
    dl1_parameters=False,
    dl1_muons=False,
    dl2=False,
    simulated=False,
    true_images=True,
    true_parameters=False,
    instrument=True,
    observation_info=False,
)

lst_events

# %%
# Loading telescope events by type returns a dict with the different telescope types:

telescope_events_by_type = loader.read_telescope_events_by_type(
    telescopes=["LST_LST_LSTCam", "MST_MST_FlashCam"],
    start=None,
    stop=None,
    dl1_images=True,
    dl1_parameters=False,
    dl1_muons=False,
    dl2=False,
    simulated=False,
    true_images=True,
    true_parameters=False,
    instrument=True,
    observation_info=False,
)

for tel_type, table in telescope_events_by_type.items():
    print(f"Telescope Type: {tel_type} \n", table, "\n")

# %%
# Loading telescope events by ID returns a dict with the different telescope IDs:

telescope_events_by_id = loader.read_telescope_events_by_id(
    telescopes=[3, 14],
    start=None,
    stop=None,
    dl1_images=True,
    dl1_parameters=False,
    dl1_muons=False,
    dl2=False,
    simulated=False,
    true_images=True,
    true_parameters=False,
    instrument=True,
    observation_info=False,
)
for tel_id, table in telescope_events_by_id.items():
    print(f"Telescope ID: {tel_id} \n", table, "\n")

# %%
# - ``read_telescope_events_chunked``
# - ``read_telescope_events_by_type_chunked``
# - ``read_telescope_events_by_id_chunked``
#
# are also available.

# %%
# Reading e.g. simulation- or observation-information:

simulation_configuration = loader.read_simulation_configuration()
observation_information = loader.read_observation_information()

simulation_configuration

# %%
# Now you have ``astropy.table`` s including all the relevant data you need
# for your analyses.
#
