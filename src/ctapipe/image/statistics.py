"""Calculating statistics of image parameters."""
import astropy.units as u
import numpy as np
from astropy.table import Table, vstack
from numba import njit

from ..containers import (
    ArrayEventContainer,
    BaseStatisticsContainer,
    StatisticsContainer,
)
from ..core import Component, FeatureGenerator, QualityQuery
from ..core.traits import List, TraitError, Tuple, Unicode
from ..vectorization import (
    collect_features,
    get_subarray_index,
    max_ufunc,
    min_ufunc,
    weighted_mean_std_ufunc,
)

__all__ = ["descriptive_statistics", "skewness", "kurtosis", "FeatureAggregator"]


@njit(cache=True)
def skewness(data, mean=None, std=None):
    """Calculate skewnewss (normalized third central moment)
    with allowing precomputed mean and std.

    With precomputed mean and std, this is ~10x faster than scipy.stats.skew
    for our use case (1D arrays with ~100-1000 elements)

    njit provides ~10% improvement over the non-jitted function.

    Parameters
    ----------
    data: ndarray
        Data for which skewness is calculated
    mean: float or None
        pre-computed mean, if not given, mean is computed
    std: float or None
        pre-computed std, if not given, std is computed

    Returns
    -------
    skewness: float
        computed skewness
    """
    if mean is None:
        mean = np.mean(data)

    if std is None:
        std = np.std(data)

    return np.mean(((data - mean) / std) ** 3)


@njit(cache=True)
def kurtosis(data, mean=None, std=None, fisher=True):
    """Calculate kurtosis (normalized fourth central moment)
    with allowing precomputed mean and std.

    With precomputed mean and std, this is ~10x faster than scipy.stats.skew
    for our use case (1D arrays with ~100-1000 elements)

    njit provides ~10% improvement over the non-jitted function.

    Parameters
    ----------
    data: ndarray
        Data for which skewness is calculated
    mean: float or None
        pre-computed mean, if not given, mean is computed
    std: float or None
        pre-computed std, if not given, std is computed
    fisher: bool
        If True, Fisher’s definition is used (normal ==> 0.0).
        If False, Pearson’s definition is used (normal ==> 3.0).

    Returns
    -------
    kurtosis: float
        kurtosis
    """
    if mean is None:
        mean = np.mean(data)

    if std is None:
        std = np.std(data)

    kurt = np.mean(((data - mean) / std) ** 4)
    if fisher is True:
        kurt -= 3.0
    return kurt


def descriptive_statistics(
    values, container_class=StatisticsContainer
) -> StatisticsContainer:
    """compute intensity statistics of an image"""
    mean = values.mean()
    std = values.std()
    return container_class(
        max=values.max(),
        min=values.min(),
        mean=mean,
        std=std,
        skewness=skewness(values, mean=mean, std=std),
        kurtosis=kurtosis(values, mean=mean, std=std),
    )


class FeatureAggregator(Component):
    """Array-event-wise aggregation of image parameters."""

    image_parameters = List(
        Tuple(Unicode(), Unicode()),
        default_value=[],
        help=(
            "List of 2-Tuples of Strings: ('prefix', 'feature'). "
            "The image parameter to be aggregated is 'prefix_feature'."
        ),
    ).tag(config=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.feature_generator = FeatureGenerator(parent=self)
        self.quality_query = QualityQuery(parent=self)

    def __call__(self, event: ArrayEventContainer) -> None:
        """Fill event container with aggregated image parameters."""
        table = None
        for tel_id in event.dl1.tel.keys():
            t = collect_features(event, tel_id)
            t["obs_id"] = event.index.obs_id
            t["event_id"] = event.index.event_id
            if not table:
                table = t
            else:
                table = vstack([table, t])

        agg_table = self.aggregate_table(table)
        for col in [
            prefix + "_" + feature for prefix, feature in self.image_parameters
        ]:
            event.dl1.aggregate[col] = BaseStatisticsContainer(
                max=agg_table[col + "_max"],
                min=agg_table[col + "_min"],
                mean=agg_table[col + "_mean"],
                std=agg_table[col + "_std"],
                prefix=col,
            )

    def aggregate_table(self, mono_parameters: Table) -> Table:
        """
        Construct table containing aggregated image parameters
        from table of telescope events.
        """
        if len(self.image_parameters) == 0:
            raise TraitError("No DL1 image parameters to aggregate are specified.")

        mono_parameters = self.feature_generator(mono_parameters)
        passes_cuts = self.quality_query.get_table_mask(mono_parameters)

        obs_ids, event_ids, multiplicity, tel_to_array_indices = get_subarray_index(
            mono_parameters
        )
        n_array_events = len(obs_ids)
        agg_table = Table({"obs_id": obs_ids, "event_id": event_ids})
        # copy metadata
        for colname in ("obs_id", "event_id"):
            agg_table[colname].description = mono_parameters[colname].description

        for prefix, feature in self.image_parameters:
            if feature in ("psi", "phi"):
                raise NotImplementedError(
                    "Aggregating rotation angels or polar coordinates"
                    " is not supported."
                )

            col = prefix + "_" + feature
            unit = mono_parameters[col].quantity.unit
            if prefix == "morphology":
                valid = mono_parameters[col] >= 0 & passes_cuts
            else:
                valid = ~np.isnan(mono_parameters[col]) & passes_cuts

            if np.sum(valid) > 0:
                means, stds = weighted_mean_std_ufunc(
                    mono_parameters[col],
                    valid,
                    n_array_events,
                    tel_to_array_indices,
                    multiplicity,
                )
                max_values = max_ufunc(
                    mono_parameters[col],
                    valid,
                    n_array_events,
                    tel_to_array_indices,
                )
                min_values = min_ufunc(
                    mono_parameters[col],
                    valid,
                    n_array_events,
                    tel_to_array_indices,
                )
            else:
                means = np.full(n_array_events, np.nan)
                stds = np.full(n_array_events, np.nan)
                max_values = np.full(n_array_events, np.nan)
                min_values = np.full(n_array_events, np.nan)

            agg_table[col + "_max"] = u.Quantity(max_values, unit, copy=False)
            agg_table[col + "_min"] = u.Quantity(min_values, unit, copy=False)
            agg_table[col + "_mean"] = u.Quantity(means, unit, copy=False)
            agg_table[col + "_std"] = u.Quantity(stds, unit, copy=False)

        return agg_table
