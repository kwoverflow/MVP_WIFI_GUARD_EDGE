"""Exact linear interpolation in one GIL-free pass, without fastmath.

The oracle is one numpy.interp call per carrier followed by float32 conversion.
Keep the original grid, endpoint behavior and double-precision arithmetic.
"""
import numpy as np
from numba import njit


@njit(cache=True, nogil=True)
def interpolate_carriers(times, amplitude, grid):
    output = np.empty((len(grid), amplitude.shape[1]), dtype=np.float32)
    left = 0
    for row in range(len(grid)):
        point = grid[row]
        while left + 1 < len(times) and times[left + 1] <= point:
            left += 1
        for column in range(amplitude.shape[1]):
            start = np.float64(amplitude[left, column])
            if point <= times[0]:
                value = np.float64(amplitude[0, column])
            elif left == len(times) - 1 or point == times[left]:
                value = start
            else:
                slope = (np.float64(amplitude[left + 1, column]) - start) / (times[left + 1] - times[left])
                value = slope * (point - times[left]) + start
            output[row, column] = value
    return output


def warmup():
    times = np.asarray([0., 1.], dtype=np.float64)
    grid = np.asarray([0., .5, 1.], dtype=np.float64)
    # Ring snapshots are C-order; training-fixed slicing can be F-order.
    for order in ('C', 'F'):
        interpolate_carriers(times, np.ones((2, 30), dtype=np.float32, order=order), grid)
