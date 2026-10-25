import pandas as pd


def split_temporal_samples(samples, validation_start, test_start, horizon_hours=0):
    """Split samples by target date, leaving each forecast horizon before a boundary out."""
    if not isinstance(samples.index, pd.DatetimeIndex):
        raise TypeError("samples must have a DatetimeIndex.")
    if horizon_hours < 0:
        raise ValueError("horizon_hours must not be negative.")

    validation_start = pd.Timestamp(validation_start)
    test_start = pd.Timestamp(test_start)
    if validation_start >= test_start:
        raise ValueError("validation_start must be earlier than test_start.")

    horizon = pd.Timedelta(hours=horizon_hours)
    train = samples.loc[samples.index < validation_start - horizon]
    validation = samples.loc[
        (samples.index >= validation_start)
        & (samples.index < test_start - horizon)
    ]
    test = samples.loc[samples.index >= test_start]

    if train.empty or validation.empty or test.empty:
        raise ValueError(
            "Temporal split produced an empty train, validation, or test set."
        )
    return train, validation, test


def fit_model_matrix(samples, test_start, horizon_hours=0):
    """Return all pre-test samples for final training, with target leakage excluded."""
    if not isinstance(samples.index, pd.DatetimeIndex):
        raise TypeError("samples must have a DatetimeIndex.")
    if horizon_hours < 0:
        raise ValueError("horizon_hours must not be negative.")

    test_start = pd.Timestamp(test_start)
    fit = samples.loc[
        samples.index < test_start - pd.Timedelta(hours=horizon_hours)
    ]
    if fit.empty:
        raise ValueError("Final training set is empty.")
    return fit
