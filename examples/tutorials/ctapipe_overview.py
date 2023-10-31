"""
Analyzing Events Using ctapipe
==============================

Initially presented @ LST Analysis Bootcamp in Padova, 26.11.2018
by Maximilian Linhoff (@maxnoe) & Kai A. Brügge (@mackaiver).

Updated since to stay compatible with current ctapipe.
"""
import tempfile
import timeit

import astropy.units as u
import matplotlib.pyplot as plt
import numpy as np
from astropy.coordinates import AltAz, angular_separation
from matplotlib.colors import ListedColormap
from scipy.sparse.csgraph import connected_components
from traitlets.config import Config

from ctapipe.calib import CameraCalibrator
from ctapipe.image import (
    ImageProcessor,
    camera_to_shower_coordinates,
    concentration_parameters,
    hillas_parameters,
    leakage_parameters,
    number_of_islands,
    timing_parameters,
    toymodel,
)
from ctapipe.image.cleaning import TailcutsImageCleaner
from ctapipe.io import DataWriter, EventSource, TableLoader
from ctapipe.reco import ShowerProcessor
from ctapipe.utils.datasets import get_dataset_path
from ctapipe.visualization import ArrayDisplay, CameraDisplay

# %matplotlib inline
# %%
plt.rcParams["figure.figsize"] = (12, 8)
plt.rcParams["font.size"] = 14
plt.rcParams["figure.figsize"]

# %%
# General Information
# -------------------
#
# Design
# ~~~~~~
#
# -  DL0 → DL3 analysis
#
# -  Currently some R0 → DL2 code to be able to analyze simtel files
#
# -  ctapipe is built upon the Scientific Python Stack, core dependencies
#    are
#
#    -  numpy
#    -  scipy
#    -  astropy
#    -  numba
#
# Developement
# ~~~~~~~~~~~~
#
# -  ctapipe is developed as Open Source Software (BSD 3-Clause License)
#    at https://github.com/cta-observatory/ctapipe
#
# -  We use the “Github-Workflow”:
#
#    -  Few people (e.g. @kosack, @maxnoe) have write access to the main
#       repository
#    -  Contributors fork the main repository and work on branches
#    -  Pull Requests are merged after Code Review and automatic execution
#       of the test suite
#
# -  Early developement stage ⇒ backwards-incompatible API changes might
#    and will happen
#
# What’s there?
# ~~~~~~~~~~~~~
#
# -  Reading simtel simulation files
# -  Simple calibration, cleaning and feature extraction functions
# -  Camera and Array plotting
# -  Coordinate frames and transformations
# -  Stereo-reconstruction using line intersections
#
#
# What’s still missing?
# ~~~~~~~~~~~~~~~~~~~~~
#
# -  IRF calculation
# -  Documentation, e.g. formal definitions of coordinate frames
#
# What can you do?
# ~~~~~~~~~~~~~~~~
#
# -  Report issues
#
#    -  Hard to get started? Tell us where you are stuck
#    -  Tell user stories
#    -  Missing features
#
# -  Start contributing
#
#    -  ctapipe needs more workpower
#    -  Implement new reconstruction features
#
# %%
# A simple Hillas analysis
# ------------------------
#
# Reading in simtel files
# ~~~~~~~~~~~~~~~~~~~~~~~
input_url = get_dataset_path("gamma_prod5.simtel.zst")
# %%
# EventSource() automatically detects what kind of file we are giving it,
# if already supported by ctapipe or an installed plugin
with EventSource(input_url, max_events=5) as source:

    print(type(source))

    for event in source:
        print(
            "Id: {}, E = {:1.3f}, Telescopes: {}".format(
                event.count, event.simulation.shower.energy, len(event.tel)
            )
        )

# %%
# Each event is a ``SubarrayEventContainer`` holding several ``Field`` s of data,
# which can be containers or just numbers. Let’s look a one event:
event

# %%
source.subarray.camera_types

# %%
# Telescope-wise data is stored in ``TelescopeEventContainer``s
# under ``.tel``, which is a mapping by telescope id:
len(event.tel), event.tel.keys()

# %%
event.tel[3]

# %%
# Data calibration
# ~~~~~~~~~~~~~~~~
#
# The ``CameraCalibrator`` calibrates the event (obtaining the ``dl1``
# images).
calibrator = CameraCalibrator(subarray=source.subarray)

# %%
calibrator(event)

# %%
# Event displays
# ~~~~~~~~~~~~~~
#
# Let’s use ctapipe’s plotting facilities to plot the telescope images
tel_id = 130

# %%
geometry = source.subarray.tel[tel_id].camera.geometry
dl1 = event.tel[tel_id].dl1

print(geometry)
print(dl1)

# %%
dl1.image

# %%
display = CameraDisplay(geometry)
display.image = dl1.image
display.add_colorbar()

# %%
# Image Cleaning
# ~~~~~~~~~~~~~~
#
# ctapipe allows most configuration options to be configured by telescope type:
# %%
print(source.subarray.telescope_types)
# %%
cleaning = TailcutsImageCleaner(
    source.subarray,
    picture_threshold_pe=[
        ("type", "*", 7),  # global default
        ("type", "MST_MST_NectarCam", 8),
        ("type", "SST_ASTRI_CHEC", 4),
    ],
    boundary_threshold_pe=[
        ("type", "*", 3.5),
        ("type", "MST_MST_NectarCam", 4),
        ("type", "SST_ASTRI_CHEC", 2),
    ],
)
# %%
image_mask = cleaning(
    tel_id,
    dl1.image,
)

# %%
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))

d1 = CameraDisplay(geometry, ax=ax1)
d2 = CameraDisplay(geometry, ax=ax2)

ax1.set_title("Image")
d1.image = dl1.image
d1.highlight_pixels(image_mask, color="w")
d1.add_colorbar(ax=ax1)

ax2.set_title("Pulse Time")
d2.image = dl1.peak_time - np.average(dl1.peak_time, weights=dl1.image)
d2.cmap = "RdBu_r"
d2.add_colorbar(ax=ax2)
d2.set_limits_minmax(-20, 20)


# %%
# Image Parameters
# ~~~~~~~~~~~~~~~~
#
hillas = hillas_parameters(geometry[image_mask], dl1.image[image_mask])

print(hillas)

# %%
plt.figure()
display = CameraDisplay(geometry, image=dl1.image)
display.highlight_pixels(image_mask, color="w")
display.add_colorbar()
display.overlay_moments(hillas, color="xkcd:red", n_sigma=2)

# %%
timing = timing_parameters(geometry, dl1.image, dl1.peak_time, hillas, image_mask)
print(timing)

# %%
long, trans = camera_to_shower_coordinates(
    geometry.pix_x, geometry.pix_y, hillas.x, hillas.y, hillas.psi
)

plt.figure()
plt.plot(long[image_mask], dl1.peak_time[image_mask], "o")
plt.plot(long[image_mask], timing.slope * long[image_mask] + timing.intercept)

# %%
leakage = leakage_parameters(geometry, dl1.image, image_mask)
print(leakage)

# %%
disp = CameraDisplay(geometry)
disp.image = dl1.image
disp.highlight_pixels(geometry.get_border_pixel_mask(1), linewidth=2, color="xkcd:red")

# %%
n_islands, island_id = number_of_islands(geometry, image_mask)

print(n_islands)

# %%
conc = concentration_parameters(geometry, dl1.image, hillas)
print(conc)


######################################################################
# Putting it all together / Stereo reconstruction
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# All these steps are now unified in several components configurable
# through the config system, mainly:
#
# -  CameraCalibrator for DL0 → DL1 (Images)
# -  ImageProcessor for DL1 (Images) → DL1 (Parameters)
# -  ShowerProcessor for stereo reconstruction of the shower geometry
# -  DataWriter for writing data into HDF5
#
# A command line tool doing these steps and writing out data in HDF5
# format is available as ``ctapipe-process``
#


image_processor_config = Config(
    {
        "ImageProcessor": {
            "image_cleaner_type": "TailcutsImageCleaner",
            "TailcutsImageCleaner": {
                "picture_threshold_pe": [
                    ("type", "LST_LST_LSTCam", 7.5),
                    ("type", "MST_MST_FlashCam", 8),
                    ("type", "MST_MST_NectarCam", 8),
                    ("type", "SST_ASTRI_CHEC", 7),
                ],
                "boundary_threshold_pe": [
                    ("type", "LST_LST_LSTCam", 5),
                    ("type", "MST_MST_FlashCam", 4),
                    ("type", "MST_MST_NectarCam", 4),
                    ("type", "SST_ASTRI_CHEC", 4),
                ],
            },
        }
    }
)

input_url = get_dataset_path("gamma_prod5.simtel.zst")

with EventSource(input_url) as source:

    calibrator = CameraCalibrator(subarray=source.subarray)
    image_processor = ImageProcessor(
        subarray=source.subarray, config=image_processor_config
    )
    shower_processor = ShowerProcessor(subarray=source.subarray)
    horizon_frame = AltAz()

    f = tempfile.NamedTemporaryFile(suffix=".hdf5")

    with DataWriter(
        source, output_path=f.name, overwrite=True, write_showers=True
    ) as writer:

        for event in source:
            energy = event.simulation.shower.energy
            n_telescopes = len(event.tel)
            event_id = event.index.event_id
            print(f"Id: {event_id}, E = {energy:1.3f}, Telescopes: {n_telescopes}")

            calibrator(event)
            image_processor(event)
            shower_processor(event)

            stereo = event.dl2.geometry["HillasReconstructor"]
            if stereo.is_valid:
                print("  Alt: {:.2f}°".format(stereo.alt.deg))
                print("  Az: {:.2f}°".format(stereo.az.deg))
                print("  Hmax: {:.0f}".format(stereo.h_max))
                print("  CoreX: {:.1f}".format(stereo.core_x))
                print("  CoreY: {:.1f}".format(stereo.core_y))
                print("  Multiplicity: {:d}".format(len(stereo.telescopes)))

            # save a nice event for plotting later
            if event.count == 3:
                plotting_event = event

            writer(event)


######################################################################
loader = TableLoader(f.name)

events = loader.read_subarray_events()

# %%
theta = angular_separation(
    events["HillasReconstructor_az"].quantity,
    events["HillasReconstructor_alt"].quantity,
    events["true_az"].quantity,
    events["true_alt"].quantity,
)

plt.hist(theta.to_value(u.deg) ** 2, bins=25, range=[0, 0.3])
plt.xlabel(r"$\theta² / deg²$")
None


######################################################################
# ArrayDisplay
# ------------
#

angle_offset = plotting_event.pointing.azimuth

plotting_hillas = {}
plotting_core = {}
for tel_id, tel_event in plotting_event.tel.items():
    plotting_hillas[tel_id] = tel_event.dl1.parameters.hillas
    plotting_core[tel_id] = tel_event.dl1.parameters.core.psi


disp = ArrayDisplay(source.subarray)

disp.set_line_hillas(plotting_hillas, plotting_core, 500)

plt.scatter(
    plotting_event.simulation.shower.core_x,
    plotting_event.simulation.shower.core_y,
    s=200,
    c="k",
    marker="x",
    label="True Impact",
)
plt.scatter(
    plotting_event.dl2.geometry["HillasReconstructor"].core_x,
    plotting_event.dl2.geometry["HillasReconstructor"].core_y,
    s=200,
    c="r",
    marker="x",
    label="Estimated Impact",
)

plt.legend()

######################################################################
# Reading the LST dl1 data
# ~~~~~~~~~~~~~~~~~~~~~~~~
#

loader = TableLoader(f.name)

dl1_table = loader.read_telescope_events(
    ["LST_LST_LSTCam"],
    dl2=False,
    true_parameters=False,
)

# %%
plt.scatter(
    np.log10(dl1_table["true_energy"].quantity / u.TeV),
    np.log10(dl1_table["hillas_intensity"]),
)
plt.xlabel("log10(E / TeV)")
plt.ylabel("log10(intensity)")
None


######################################################################
# Isn’t python slow?
# ------------------
#
# -  Many of you might have heard: “Python is slow”.
# -  That’s trueish.
# -  All python objects are classes living on the heap, even integers.
# -  Looping over lots of “primitives” is quite slow compared to other
#    languages.
#
# | ⇒ Vectorize as much as possible using numpy
# | ⇒ Use existing interfaces to fast C / C++ / Fortran code
# | ⇒ Optimize using numba
#
# **But: “Premature Optimization is the root of all evil” — Donald Knuth**
#
# So profile to find exactly what is slow.
#
# Why use python then?
# ~~~~~~~~~~~~~~~~~~~~
#
# -  Python works very well as *glue* for libraries of all kinds of
#    languages
# -  Python has a rich ecosystem for data science, physics, algorithms,
#    astronomy
#
# Example: Number of Islands
# ~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# Find all groups of pixels, that survived the cleaning
#


geometry = loader.subarray.tel[1].camera.geometry


######################################################################
# Let’s create a toy images with several islands;
#

rng = np.random.default_rng(42)

image = np.zeros(geometry.n_pixels)


for i in range(9):
    model = toymodel.Gaussian(
        x=rng.uniform(-0.8, 0.8) * u.m,
        y=rng.uniform(-0.8, 0.8) * u.m,
        width=rng.uniform(0.05, 0.075) * u.m,
        length=rng.uniform(0.1, 0.15) * u.m,
        psi=rng.uniform(0, 2 * np.pi) * u.rad,
    )

    new_image, sig, bg = model.generate_image(
        geometry, intensity=np.random.uniform(1000, 3000), nsb_level_pe=5
    )
    image += new_image

# %%
image_mask = cleaning(
    tel_id=1,
    image=image,
)

# %%
disp = CameraDisplay(geometry)
disp.image = image
disp.highlight_pixels(image_mask, color="xkcd:red", linewidth=1.5)
disp.add_colorbar()


# %%
def num_islands_python(camera, clean):
    """A breadth first search to find connected islands of neighboring pixels in the cleaning set"""

    # the camera geometry has a [n_pixel, n_pixel] boolean array
    # that is True where two pixels are neighbors
    neighbors = camera.neighbor_matrix

    island_ids = np.zeros(camera.n_pixels)
    current_island = 0

    # a set to remember which pixels we already visited
    visited = set()

    # go only through the pixels, that survived cleaning
    for pix_id in np.where(clean)[0]:
        if pix_id not in visited:
            # remember that we already checked this pixel
            visited.add(pix_id)

            # if we land in the outer loop again, we found a new island
            current_island += 1
            island_ids[pix_id] = current_island

            # now check all neighbors of the current pixel recursively
            to_check = set(np.where(neighbors[pix_id] & clean)[0])
            while to_check:
                pix_id = to_check.pop()

                if pix_id not in visited:
                    visited.add(pix_id)
                    island_ids[pix_id] = current_island

                    to_check.update(np.where(neighbors[pix_id] & clean)[0])

    n_islands = current_island
    return n_islands, island_ids


# %%
n_islands, island_ids = num_islands_python(geometry, image_mask)

# %%
cmap = plt.get_cmap("Paired")
cmap = ListedColormap(cmap.colors[:n_islands])
cmap.set_under("k")

disp = CameraDisplay(geometry)
disp.image = island_ids
disp.cmap = cmap
disp.set_limits_minmax(0.5, n_islands + 0.5)
disp.add_colorbar()

# %%
timeit.timeit(lambda: num_islands_python(geometry, image_mask), number=1000) / 1000

# %%


def num_islands_scipy(geometry, clean):
    neighbors = geometry.neighbor_matrix_sparse

    clean_neighbors = neighbors[clean][:, clean]
    num_islands, labels = connected_components(clean_neighbors, directed=False)

    island_ids = np.zeros(geometry.n_pixels)
    island_ids[clean] = labels + 1

    return num_islands, island_ids


# %%
n_islands_s, island_ids_s = num_islands_scipy(geometry, image_mask)

# %%
disp = CameraDisplay(geometry)
disp.image = island_ids_s
disp.cmap = cmap
disp.set_limits_minmax(0.5, n_islands_s + 0.5)
disp.add_colorbar()

# %%
timeit.timeit(lambda: num_islands_scipy(geometry, image_mask), number=10000) / 10000

# %%
# **A lot less code, and a factor 3 speed improvement**
#
# Finally, current ctapipe implementation is using numba:
#
timeit.timeit(lambda: number_of_islands(geometry, image_mask), number=100000) / 100000
