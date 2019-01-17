from functools import total_ordering

import numpy as np
from pandas.api.extensions import (
    ExtensionDtype, ExtensionArray, register_extension_dtype)
from numbers import Integral

from pandas.api.types import pandas_dtype
from pandas.core.dtypes.common import is_extension_array_dtype


def _validate_ragged_properties(data):
    """
    Validate that dict contains the necessary properties to construct a
    RaggedArray.

    Parameters
    ----------
    data: dict
        A dict containing 'start_indices' and 'flat_array' keys
        with numpy array values

    Raises
    ------
    ValueError:
        if input contains invalid or incompatible properties
    """

    # Validate start_indices
    start_indices = data['start_indices']

    if (not isinstance(start_indices, np.ndarray) or
            start_indices.dtype.kind != 'u' or
            start_indices.ndim != 1):
        raise ValueError("""
The start_indices property of a RaggedArray must be a 1D numpy array of
unsigned integers (start_indices.dtype.kind == 'u')
    Received value of type {typ}: {v}""".format(
            typ=type(start_indices), v=repr(start_indices)))

    # Validate flat_array
    flat_array = data['flat_array']

    if (not isinstance(flat_array, np.ndarray) or
            flat_array.ndim != 1):
        raise ValueError("""
The flat_array property of a RaggedArray must be a 1D numpy array
    Received value of type {typ}: {v}""".format(
            typ=type(flat_array), v=repr(flat_array)))

    # Validate start_indices values
    # We don't need to check start_indices < 0 because we already know that it
    # has an unsigned integer datatype
    #
    # Note that start_indices[i] == len(flat_array) is valid as it represents
    # and empty array element at the end of the ragged array.
    invalid_inds = start_indices > len(flat_array)

    if invalid_inds.any():
        some_invalid_vals = start_indices[invalid_inds[:10]]

        raise ValueError("""
Elements of start_indices must be less than the length of flat_array ({m})
    Invalid values include: {vals}""".format(
            m=len(flat_array), vals=repr(some_invalid_vals)))


# Internal ragged element array wrapper that provides
# equality, ordering, and hashing.
@total_ordering
class RaggedElement(object):

    @staticmethod
    def ragged_or_nan(a):
        if np.isscalar(a) and np.isnan(a):
            return a
        else:
            return RaggedElement(a)

    @staticmethod
    def array_or_nan(a):
        if np.isscalar(a) and np.isnan(a):
            return a
        else:
            return a.array

    def __init__(self, array):
        self.array = array

    def __hash__(self):
        # TODO: Rewrite using self.array directly without tuple
        return hash(tuple(self.array))

    def __eq__(self, other):
        if not isinstance(other, RaggedElement):
            return False
        return np.array_equal(self.array, other.array)

    def __lt__(self, other):
        # TODO: Rewrite using self.array directly without tuples
        if not isinstance(other, RaggedElement):
            return NotImplemented
        return tuple(self.array) < tuple(other.array)

    def __repr__(self):
        array_repr = repr(self.array)
        return array_repr.replace('array', 'ragged_element')


@register_extension_dtype
class RaggedDtype(ExtensionDtype):
    name = 'ragged'
    type = np.ndarray
    base = np.dtype('O')

    @classmethod
    def construct_array_type(cls):
        return RaggedArray

    @classmethod
    def construct_from_string(cls, string):
        if string == 'ragged':
            return RaggedDtype()
        else:
            raise TypeError("Cannot construct a '{}' from '{}'"
                            .format(cls, string))


def missing(v):
    return v is None or (np.isscalar(v) and np.isnan(v))


class RaggedArray(ExtensionArray):
    def __init__(self, data, dtype=None):
        """
        Construct a RaggedArray

        Parameters
        ----------
        data: list or array or dict or RaggedArray
            * list or 1D-array: A List or 1D array of lists or 1D arrays that
                                should be represented by the RaggedArray

            * dict: A dict containing 'start_indices' and 'flat_array' keys
                    with numpy array values where:
                    - flat_array:  numpy array containing concatenation
                                   of all nested arrays to be represented
                                   by this ragged array
                    - start_indices: unsiged integer numpy array the same
                                     length as the ragged array where values
                                     represent the index into flat_array where
                                     the corresponding ragged array element
                                     begins
            * RaggedArray: A RaggedArray instance to copy

        dtype: np.dtype or str or None (default None)
            Datatype to use to store underlying values from data.
            If none (the default) then dtype will be determined using the
            numpy.result_type function.
        """
        self._dtype = RaggedDtype()
        if (isinstance(data, dict) and
                all(k in data for k in
                    ['start_indices', 'flat_array'])):

            _validate_ragged_properties(data)

            self._start_indices = data['start_indices']
            self._flat_array = data['flat_array']
        elif isinstance(data, RaggedArray):
            self._flat_array = data.flat_array.copy()
            self._start_indices = data.start_indices.copy()
        else:
            # Compute lengths
            index_len = len(data)
            buffer_len = sum(len(datum)
                             if not missing(datum)
                             else 0 for datum in data)

            # Compute necessary precision of start_indices array
            for nbits in [8, 16, 32, 64]:
                start_indices_dtype = 'uint' + str(nbits)
                max_supported = np.iinfo(start_indices_dtype).max
                if buffer_len <= max_supported:
                    break

            # infer dtype if not provided
            if dtype is None:
                non_missing = [np.atleast_1d(v)
                               for v in data if not missing(v)]
                if non_missing:
                    dtype = np.result_type(*non_missing)
                else:
                    dtype = 'float64'

            # Initialize representation arrays
            self._start_indices = np.zeros(index_len, dtype=start_indices_dtype)
            self._flat_array = np.zeros(buffer_len, dtype=dtype)

            # Populate arrays
            next_start_ind = 0
            for i, array_el in enumerate(data):
                # Compute element length
                n = len(array_el) if not missing(array_el) else 0

                # Update start indices
                self._start_indices[i] = next_start_ind

                # Update flat array
                self._flat_array[next_start_ind:next_start_ind+n] = array_el

                # increment next start index
                next_start_ind += n

    @property
    def flat_array(self):
        """
        numpy array containing concatenation of all nested arrays

        Returns
        -------
        np.ndarray
        """
        return self._flat_array

    @property
    def start_indices(self):
        """
        unsiged integer numpy array the same length as the ragged array where
        values represent the index into flat_array where the corresponding
        ragged array element begins

        Returns
        -------
        np.ndarray
        """
        return self._start_indices

    def __len__(self):
        """
        Length of this array

        Returns
        -------
        length : int
        """
        return len(self._start_indices)

    def __getitem__(self, item):
        """
        Parameters
        ----------
        item : int, slice, or ndarray
            * int: The position in 'self' to get.

            * slice: A slice object, where 'start', 'stop', and 'step' are
              integers or None

            * ndarray: A 1-d boolean NumPy ndarray the same length as 'self'
        """
        if isinstance(item, Integral):
            if item < -len(self) or item >= len(self):
                raise IndexError("{item} is out of bounds".format(item=item))
            else:
                # Convert negative item index
                if item < 0:
                    item = len(self) + item

                slice_start = self.start_indices[item]
                slice_end = (self.start_indices[item+1]
                             if item + 1 <= len(self) - 1
                             else len(self.flat_array))

                return (self.flat_array[slice_start:slice_end]
                        if slice_end!=slice_start
                        else np.nan)

        elif type(item) == slice:
            data = []
            selected_indices = np.arange(len(self))[item]

            for selected_index in selected_indices:
                data.append(self[selected_index])

            return RaggedArray(data, dtype=self.flat_array.dtype)

        elif isinstance(item, np.ndarray) and item.dtype == 'bool':
            data = []

            for i, m in enumerate(item):
                if m:
                    data.append(self[i])

            return RaggedArray(data, dtype=self.flat_array.dtype)
        elif isinstance(item, (list, np.ndarray)):
            return self.take(item, allow_fill=False)
        else:
            raise IndexError(item)

    @classmethod
    def _from_sequence(cls, scalars, dtype=None, copy=False):
        """
        Construct a new RaggedArray from a sequence of scalars.

        Parameters
        ----------
        scalars : Sequence
            Each element will be an instance of the scalar type for this
            array, ``cls.dtype.type``.
        dtype : dtype, optional
            Construct for this particular dtype. This should be a Dtype
            compatible with the ExtensionArray.
        copy : boolean, default False
            If True, copy the underlying data.

        Returns
        -------
        RaggedArray
        """
        return RaggedArray(scalars)

    @classmethod
    def _from_factorized(cls, values, original):
        """
        Reconstruct an ExtensionArray after factorization.

        Parameters
        ----------
        values : ndarray
            An integer ndarray with the factorized values.
        original : RaggedArray
            The original ExtensionArray that factorize was called on.

        See Also
        --------
        pandas.factorize
        ExtensionArray.factorize
        """
        return RaggedArray(
            [RaggedElement.array_or_nan(v) for v in values],
            dtype=original.flat_array.dtype)

    def _as_ragged_element_array(self):
        return np.array([RaggedElement.ragged_or_nan(self[i])
                         for i in range(len(self))])

    def _values_for_factorize(self):
        return self._as_ragged_element_array(), np.nan

    def _values_for_argsort(self):
        return self._as_ragged_element_array()

    def unique(self):
        """
        Compute the ExtensionArray of unique values.

        Returns
        -------
        uniques : ExtensionArray
        """
        from pandas import unique

        uniques = unique(self._as_ragged_element_array())
        return self._from_sequence(
            [RaggedElement.array_or_nan(v) for v in uniques],
            dtype=self.dtype)

    def fillna(self, value=None, method=None, limit=None):
        """
        Fill NA/NaN values using the specified method.

        Parameters
        ----------
        value : scalar, array-like
            If a scalar value is passed it is used to fill all missing values.
            Alternatively, an array-like 'value' can be given. It's expected
            that the array-like have the same length as 'self'.
        method : {'backfill', 'bfill', 'pad', 'ffill', None}, default None
            Method to use for filling holes in reindexed Series
            pad / ffill: propagate last valid observation forward to next valid
            backfill / bfill: use NEXT valid observation to fill gap
        limit : int, default None
            If method is specified, this is the maximum number of consecutive
            NaN values to forward/backward fill. In other words, if there is
            a gap with more than this number of consecutive NaNs, it will only
            be partially filled. If method is not specified, this is the
            maximum number of entries along the entire axis where NaNs will be
            filled.

        Returns
        -------
        filled : ExtensionArray with NA/NaN filled
        """
        # Override in RaggedArray to handle ndarray fill values
        from pandas.api.types import is_array_like
        from pandas.util._validators import validate_fillna_kwargs
        from pandas.core.missing import pad_1d, backfill_1d

        value, method = validate_fillna_kwargs(value, method)

        mask = self.isna()

        if isinstance(value, RaggedArray):
            if len(value) != len(self):
                raise ValueError("Length of 'value' does not match. Got ({}) "
                                 " expected {}".format(len(value), len(self)))
            value = value[mask]

        if mask.any():
            if method is not None:
                func = pad_1d if method == 'pad' else backfill_1d
                new_values = func(self.astype(object), limit=limit,
                                  mask=mask)
                new_values = self._from_sequence(new_values, dtype=self.dtype)
            else:
                # fill with value
                new_values = list(self)
                mask_indices, = np.where(mask)
                for ind in mask_indices:
                    new_values[ind] = value

                new_values = self._from_sequence(new_values, dtype=self.dtype)
        else:
            new_values = self.copy()
        return new_values

    def shift(self, periods=1, fill_value=None):
        # type: (int, object) -> ExtensionArray
        """
        Shift values by desired number.

        Newly introduced missing values are filled with
        ``self.dtype.na_value``.

        .. versionadded:: 0.24.0

        Parameters
        ----------
        periods : int, default 1
            The number of periods to shift. Negative values are allowed
            for shifting backwards.

        fill_value : object, optional
            The scalar value to use for newly introduced missing values.
            The default is ``self.dtype.na_value``

            .. versionadded:: 0.24.0

        Returns
        -------
        shifted : ExtensionArray

        Notes
        -----
        If ``self`` is empty or ``periods`` is 0, a copy of ``self`` is
        returned.

        If ``periods > len(self)``, then an array of size
        len(self) is returned, with all values filled with
        ``self.dtype.na_value``.
        """
        # Override in RaggedArray to handle ndarray fill values

        # Note: this implementation assumes that `self.dtype.na_value` can be
        # stored in an instance of your ExtensionArray with `self.dtype`.
        if not len(self) or periods == 0:
            return self.copy()

        if fill_value is None:
            fill_value = np.nan

        empty = self._from_sequence(
            [fill_value] * min(abs(periods), len(self)),
            dtype=self.dtype
        )
        if periods > 0:
            a = empty
            b = self[:-periods]
        else:
            a = self[abs(periods):]
            b = empty
        return self._concat_same_type([a, b])

    def searchsorted(self, value, side="left", sorter=None):
        """
        Find indices where elements should be inserted to maintain order.

        .. versionadded:: 0.24.0

        Find the indices into a sorted array `self` (a) such that, if the
        corresponding elements in `v` were inserted before the indices, the
        order of `self` would be preserved.

        Assuming that `a` is sorted:

        ======  ============================
        `side`  returned index `i` satisfies
        ======  ============================
        left    ``self[i-1] < v <= self[i]``
        right   ``self[i-1] <= v < self[i]``
        ======  ============================

        Parameters
        ----------
        value : array_like
            Values to insert into `self`.
        side : {'left', 'right'}, optional
            If 'left', the index of the first suitable location found is given.
            If 'right', return the last such index.  If there is no suitable
            index, return either 0 or N (where N is the length of `self`).
        sorter : 1-D array_like, optional
            Optional array of integer indices that sort array a into ascending
            order. They are typically the result of argsort.

        Returns
        -------
        indices : array of ints
            Array of insertion points with the same shape as `value`.

        See Also
        --------
        numpy.searchsorted : Similar method from NumPy.
        """
        # Note: the base tests provided by pandas only test the basics.
        # We do not test
        # 1. Values outside the range of the `data_for_sorting` fixture
        # 2. Values between the values in the `data_for_sorting` fixture
        # 3. Missing values.
        arr = self._as_ragged_element_array()
        if isinstance(value, RaggedArray):
            search_value = value._as_ragged_element_array()
        else:
            search_value = RaggedElement(value)
        return arr.searchsorted(search_value, side=side, sorter=sorter)

    def isna(self):
        """
        A 1-D array indicating if each value is missing.

        Returns
        -------
        na_values : np.ndarray
            boolean ndarray the same length as the ragged array where values
            of True represent missing/NA values.
        """
        stop_indices = np.hstack([self.start_indices[1:],
                                  [len(self.flat_array)]])

        element_lengths = stop_indices - self.start_indices
        return element_lengths == 0

    def take(self, indices, allow_fill=False, fill_value=None):
        """
        Take elements from an array.

        Parameters
        ----------
        indices : sequence of integers
            Indices to be taken.
        allow_fill : bool, default False
            How to handle negative values in `indices`.

            * False: negative values in `indices` indicate positional indices
              from the right (the default). This is similar to
              :func:`numpy.take`.

            * True: negative values in `indices` indicate
              missing values. These values are set to `fill_value`. Any other
              other negative values raise a ``ValueError``.

        fill_value : any, default None
            Fill value to use for NA-indices when `allow_fill` is True.

        Returns
        -------
        RaggedArray

        Raises
        ------
        IndexError
            When the indices are out of bounds for the array.
        """
        if allow_fill:
            invalid_inds = [i for i in indices if i < -1]
            if invalid_inds:
                raise ValueError("""
Invalid indices for take with allow_fill True: {inds}""".format(
                    inds=invalid_inds[:9]))
            sequence = [self[i] if i >= 0 else fill_value
                        for i in indices]
        else:
            if len(self) == 0 and len(indices) > 0:
                raise IndexError("cannot do a non-empty take")

            sequence = [self[i] for i in indices]

        return RaggedArray(sequence, dtype=self.flat_array.dtype)

    def copy(self, deep=False):
        """
        Return a copy of the array.

        Parameters
        ----------
        deep : bool, default False
            Also copy the underlying data backing this array.

        Returns
        -------
        RaggedArray
        """
        data = dict(
            flat_array=self.flat_array,
            start_indices=self.start_indices)

        if deep:
            # Copy underlying numpy arrays
            data = {k: v.copy() for k, v in data.items()}

        return RaggedArray(data)

    @classmethod
    def _concat_same_type(cls, to_concat):
        """
        Concatenate multiple RaggedArray instances

        Parameters
        ----------
        to_concat : list of RaggedArray

        Returns
        -------
        RaggedArray
        """
        # concat flat_arrays
        flat_array = np.hstack(ra.flat_array for ra in to_concat)

        # offset and concat start_indices
        offsets = np.hstack([
            [0],
            np.cumsum([len(ra.flat_array) for ra in to_concat[:-1]])])

        start_indices = np.hstack([ra.start_indices + offset
                                   for offset, ra in zip(offsets, to_concat)])

        return RaggedArray(dict(
            flat_array=flat_array, start_indices=start_indices))

    @property
    def dtype(self):
        return self._dtype

    @property
    def nbytes(self):
        """
        The number of bytes needed to store this object in memory.
        """
        return (self._flat_array.nbytes +
                self._start_indices.nbytes)

    def astype(self, dtype, copy=True):

        dtype = pandas_dtype(dtype)
        if isinstance(dtype, RaggedDtype):
            if copy:
                return self.copy()
            return self

        elif is_extension_array_dtype(dtype):
            dtype.construct_array_type()._from_sequence(np.asarray(self))

        return np.array([v for v in self], dtype=dtype, copy=copy)