"""Search for a book that is considerably better than the live residual.

The bar is locked here, before the replay. On the published window a cell
has to clear 1.5 times the live pnl, match or beat each calendar year, and
not draw down deeper. It also has to beat the live book on every start day
from 2024-03-16 through 2024-03-22. One lucky start is not a result.

Nothing in this package is armed.
"""

BAR_MULT = 1.5
