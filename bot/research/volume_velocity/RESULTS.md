# Minute volume velocity inside the €200k–€300k section

Question: names whose prior-30-day median daily quote volume sits in the
€200k–€300k section, and that then print a sudden burst of volume per minute —
does buying that burst make money after Bitvavo taker fees and impact?

Window 2024-06-01 → 2026-10-16. IS through 2026-01-01, OOS after.
Membership uses only the prior 30 completed days, so the burst itself cannot
qualify the name. Fill is the next traded minute's open within 5 minutes.
One hour cooldown per name. Costs: taker 0.25% + impact 0.02*notional/baseline per side, cap 3%.
The euro PnL is the sum of fixed-size round trips; the book is not stopped at zero,
so a −€100k cell is many reused slots, not a path a small account survives.
Nothing here is armed.

## €200k–€300k, up-minute only, one €2k slot

| trigger | exit | IS | OOS |
| --- | --- | --- | --- |
| 1m x10 | hold 15m | -232,209 (n=21935, win 20%, med -0.54%, dd 232,240) | -119,928 (n=11338, win 18%, med -0.56%, dd 119,928) |
| 1m x10 | hold 60m | -107,558 (n=10025, win 29%, med -0.56%, dd 107,845) | -55,408 (n=5133, win 28%, med -0.59%, dd 55,596) |
| 1m x10 | hold 4h | -31,202 (n=3127, win 37%, med -0.57%, dd 31,331) | -9,870 (n=1562, win 37%, med -0.57%, dd 11,182) |
| 1m x10 | hold 24h | -4,179 (n=570, win 42%, med -1.01%, dd 5,921) | -2,433 (n=282, win 37%, med -1.06%, dd 4,417) |
| 1m x10 | trail 10% / 24h | -5,987 (n=640, win 43%, med -0.99%, dd 6,780) | -2,044 (n=310, win 45%, med -0.39%, dd 3,849) |
| 1m x10 | trail 10% + stop 8% | -2,875 (n=652, win 43%, med -0.91%, dd 5,287) | -2,565 (n=317, win 43%, med -1.04%, dd 4,385) |
| 1m x20 | hold 15m | -192,606 (n=18126, win 20%, med -0.55%, dd 192,637) | -97,856 (n=9305, win 18%, med -0.55%, dd 97,856) |
| 1m x20 | hold 60m | -97,125 (n=9120, win 29%, med -0.58%, dd 97,194) | -51,795 (n=4669, win 27%, med -0.59%, dd 51,831) |
| 1m x20 | hold 4h | -35,529 (n=3018, win 36%, med -0.65%, dd 35,668) | -12,811 (n=1510, win 35%, med -0.61%, dd 13,340) |
| 1m x20 | hold 24h | -11,966 (n=566, win 41%, med -1.25%, dd 12,589) | -6,610 (n=281, win 39%, med -1.08%, dd 8,633) |
| 1m x20 | trail 10% / 24h | -6,769 (n=636, win 43%, med -1.01%, dd 7,890) | -2,641 (n=319, win 41%, med -0.91%, dd 4,599) |
| 1m x20 | trail 10% + stop 8% | -10,306 (n=658, win 40%, med -1.33%, dd 10,460) | -4,172 (n=327, win 41%, med -0.98%, dd 5,671) |
| 1m x40 | hold 15m | -135,706 (n=12800, win 22%, med -0.56%, dd 135,706) | -67,209 (n=6225, win 20%, med -0.55%, dd 67,209) |
| 1m x40 | hold 60m | -75,575 (n=7421, win 30%, med -0.59%, dd 75,640) | -43,183 (n=3705, win 28%, med -0.61%, dd 43,289) |
| 1m x40 | hold 4h | -26,910 (n=2767, win 36%, med -0.66%, dd 27,215) | -18,639 (n=1386, win 34%, med -0.76%, dd 19,009) |
| 1m x40 | hold 24h | -6,512 (n=557, win 43%, med -1.03%, dd 7,529) | -6,360 (n=276, win 37%, med -1.19%, dd 7,151) |
| 1m x40 | trail 10% / 24h | -9,776 (n=644, win 39%, med -1.17%, dd 11,372) | -4,783 (n=313, win 38%, med -1.44%, dd 6,352) |
| 1m x40 | trail 10% + stop 8% | -12,249 (n=658, win 39%, med -1.38%, dd 12,330) | -2,761 (n=329, win 40%, med -1.19%, dd 4,661) |
| 1m x80 | hold 15m | -70,009 (n=6978, win 24%, med -0.55%, dd 70,012) | -31,675 (n=3000, win 22%, med -0.56%, dd 31,675) |
| 1m x80 | hold 60m | -46,392 (n=4839, win 31%, med -0.58%, dd 46,853) | -28,517 (n=2229, win 30%, med -0.64%, dd 28,546) |
| 1m x80 | hold 4h | -30,925 (n=2213, win 35%, med -0.68%, dd 31,500) | -13,339 (n=1054, win 37%, med -0.66%, dd 13,638) |
| 1m x80 | hold 24h | -1,275 (n=528, win 40%, med -1.12%, dd 6,615) | -3,999 (n=259, win 40%, med -1.17%, dd 5,923) |
| 1m x80 | trail 10% / 24h | -4,589 (n=642, win 42%, med -0.89%, dd 4,823) | -2,677 (n=322, win 43%, med -0.95%, dd 4,266) |
| 1m x80 | trail 10% + stop 8% | -4,993 (n=641, win 42%, med -0.89%, dd 5,190) | -1,814 (n=324, win 44%, med -0.92%, dd 4,028) |
| 5m x8 | hold 15m | -156,788 (n=15352, win 22%, med -0.55%, dd 156,792) | -87,953 (n=8044, win 19%, med -0.56%, dd 87,953) |
| 5m x8 | hold 60m | -90,612 (n=8315, win 30%, med -0.59%, dd 90,794) | -45,575 (n=4330, win 28%, med -0.58%, dd 45,656) |
| 5m x8 | hold 4h | -31,641 (n=2883, win 37%, med -0.65%, dd 31,885) | -22,223 (n=1465, win 34%, med -0.68%, dd 22,552) |
| 5m x8 | hold 24h | -581 (n=560, win 39%, med -1.22%, dd 4,789) | -3,106 (n=279, win 42%, med -0.73%, dd 4,705) |
| 5m x8 | trail 10% / 24h | -7,800 (n=647, win 40%, med -1.12%, dd 8,739) | -1,859 (n=325, win 43%, med -0.87%, dd 3,346) |
| 5m x8 | trail 10% + stop 8% | -4,618 (n=659, win 40%, med -1.18%, dd 5,306) | -2,836 (n=337, win 39%, med -0.97%, dd 4,594) |
| 5m x15 | hold 15m | -102,753 (n=10037, win 24%, med -0.55%, dd 102,754) | -53,316 (n=5085, win 22%, med -0.55%, dd 53,316) |
| 5m x15 | hold 60m | -62,718 (n=6353, win 31%, med -0.58%, dd 62,876) | -37,705 (n=3243, win 29%, med -0.62%, dd 37,866) |
| 5m x15 | hold 4h | -24,615 (n=2548, win 36%, med -0.64%, dd 25,155) | -14,650 (n=1288, win 36%, med -0.69%, dd 15,323) |
| 5m x15 | hold 24h | -12,017 (n=548, win 38%, med -1.52%, dd 12,054) | -128 (n=271, win 46%, med -0.57%, dd 2,173) |
| 5m x15 | trail 10% / 24h | -8,862 (n=656, win 38%, med -1.56%, dd 9,769) | -3,031 (n=340, win 41%, med -0.90%, dd 3,985) |
| 5m x15 | trail 10% + stop 8% | -10,890 (n=677, win 38%, med -1.56%, dd 11,343) | -831 (n=336, win 44%, med -0.64%, dd 2,560) |
| 5m x30 | hold 15m | -52,640 (n=5225, win 24%, med -0.56%, dd 52,659) | -24,687 (n=2320, win 23%, med -0.55%, dd 24,714) |
| 5m x30 | hold 60m | -38,194 (n=3896, win 32%, med -0.62%, dd 38,586) | -21,729 (n=1798, win 31%, med -0.61%, dd 21,946) |
| 5m x30 | hold 4h | -27,393 (n=1919, win 35%, med -0.74%, dd 27,393) | -13,653 (n=922, win 36%, med -0.67%, dd 13,801) |
| 5m x30 | hold 24h | -12,007 (n=502, win 36%, med -1.35%, dd 12,147) | -5,225 (n=244, win 41%, med -1.41%, dd 5,696) |
| 5m x30 | trail 10% / 24h | -8,810 (n=632, win 39%, med -1.33%, dd 8,950) | -3,604 (n=320, win 41%, med -1.30%, dd 3,910) |
| 5m x30 | trail 10% + stop 8% | -7,864 (n=643, win 41%, med -1.27%, dd 8,288) | -2,841 (n=327, win 40%, med -1.45%, dd 3,147) |

## Same triggers, both up and down minutes, one €2k slot

| trigger | exit | IS | OOS |
| --- | --- | --- | --- |
| clock open | hold 15m | -15,171 (n=1347, win 23%, med -0.53%, dd 15,201) | -6,218 (n=662, win 21%, med -0.50%, dd 6,230) |
| clock open | hold 60m | -10,431 (n=841, win 32%, med -0.56%, dd 10,483) | -4,171 (n=393, win 27%, med -0.53%, dd 4,171) |
| clock open | hold 4h | -7,719 (n=603, win 38%, med -0.62%, dd 7,837) | -2,106 (n=289, win 36%, med -0.56%, dd 2,727) |
| clock open | hold 24h | -8,302 (n=488, win 41%, med -0.89%, dd 8,808) | -2,742 (n=242, win 42%, med -0.74%, dd 3,914) |
| clock open | trail 10% / 24h | -6,209 (n=520, win 40%, med -0.94%, dd 6,278) | -1,284 (n=250, win 42%, med -0.72%, dd 2,039) |
| clock open | trail 10% + stop 8% | -6,559 (n=525, win 39%, med -0.92%, dd 6,628) | -1,943 (n=252, win 42%, med -0.72%, dd 2,801) |
| 1m x10 | hold 15m | -287,176 (n=27147, win 20%, med -0.54%, dd 287,207) | -149,065 (n=13969, win 18%, med -0.54%, dd 149,065) |
| 1m x10 | hold 60m | -116,557 (n=11165, win 30%, med -0.57%, dd 116,625) | -64,060 (n=5653, win 28%, med -0.59%, dd 64,065) |
| 1m x10 | hold 4h | -32,134 (n=3222, win 36%, med -0.58%, dd 32,426) | -14,795 (n=1612, win 36%, med -0.58%, dd 15,663) |
| 1m x10 | hold 24h | -4,968 (n=573, win 41%, med -0.86%, dd 5,288) | -945 (n=283, win 42%, med -0.93%, dd 3,615) |
| 1m x10 | trail 10% / 24h | -11,459 (n=651, win 41%, med -1.13%, dd 11,535) | -1,471 (n=311, win 40%, med -0.74%, dd 4,419) |
| 1m x10 | trail 10% + stop 8% | -8,234 (n=659, win 40%, med -1.12%, dd 8,405) | -671 (n=315, win 43%, med -0.70%, dd 4,156) |
| 1m x20 | hold 15m | -252,594 (n=23678, win 20%, med -0.54%, dd 252,628) | -128,154 (n=12399, win 19%, med -0.54%, dd 128,160) |
| 1m x20 | hold 60m | -109,001 (n=10488, win 30%, med -0.58%, dd 109,154) | -55,306 (n=5359, win 30%, med -0.54%, dd 55,341) |
| 1m x20 | hold 4h | -30,609 (n=3150, win 38%, med -0.59%, dd 30,826) | -18,087 (n=1583, win 34%, med -0.70%, dd 18,546) |
| 1m x20 | hold 24h | -5,684 (n=571, win 41%, med -1.14%, dd 7,527) | -4,293 (n=283, win 40%, med -1.15%, dd 5,578) |
| 1m x20 | trail 10% / 24h | -7,655 (n=658, win 38%, med -1.35%, dd 10,119) | -4,448 (n=312, win 42%, med -1.14%, dd 4,958) |
| 1m x20 | trail 10% + stop 8% | -3,427 (n=685, win 40%, med -1.23%, dd 7,556) | -3,124 (n=323, win 44%, med -0.94%, dd 3,796) |
| 1m x40 | hold 15m | -197,094 (n=18250, win 21%, med -0.55%, dd 197,094) | -97,733 (n=9277, win 20%, med -0.54%, dd 97,733) |
| 1m x40 | hold 60m | -99,577 (n=9138, win 30%, med -0.59%, dd 99,967) | -50,072 (n=4668, win 30%, med -0.56%, dd 50,075) |
| 1m x40 | hold 4h | -32,936 (n=2986, win 37%, med -0.65%, dd 33,515) | -17,306 (n=1506, win 35%, med -0.69%, dd 17,807) |
| 1m x40 | hold 24h | -7,690 (n=565, win 42%, med -0.94%, dd 9,444) | -739 (n=280, win 40%, med -0.96%, dd 4,407) |
| 1m x40 | trail 10% / 24h | -7,751 (n=657, win 40%, med -1.11%, dd 7,832) | -4,123 (n=319, win 43%, med -0.72%, dd 5,179) |
| 1m x40 | trail 10% + stop 8% | -12,010 (n=674, win 38%, med -1.44%, dd 12,284) | -2,085 (n=329, win 41%, med -0.99%, dd 4,189) |
| 1m x80 | hold 15m | -113,814 (n=10844, win 24%, med -0.54%, dd 113,814) | -53,639 (n=5117, win 22%, med -0.53%, dd 53,639) |
| 1m x80 | hold 60m | -68,231 (n=6641, win 31%, med -0.59%, dd 68,257) | -37,774 (n=3278, win 31%, med -0.57%, dd 37,905) |
| 1m x80 | hold 4h | -30,404 (n=2576, win 37%, med -0.59%, dd 30,423) | -21,359 (n=1280, win 35%, med -0.75%, dd 21,687) |
| 1m x80 | hold 24h | -917 (n=549, win 41%, med -0.78%, dd 6,335) | -2,618 (n=271, win 40%, med -1.11%, dd 4,506) |
| 1m x80 | trail 10% / 24h | -6,796 (n=661, win 39%, med -1.17%, dd 7,644) | -2,321 (n=319, win 37%, med -1.26%, dd 3,357) |
| 1m x80 | trail 10% + stop 8% | -9,298 (n=686, win 38%, med -1.46%, dd 10,146) | -5,080 (n=319, win 38%, med -1.41%, dd 5,439) |
| 5m x8 | hold 15m | -215,129 (n=20641, win 21%, med -0.54%, dd 215,133) | -116,151 (n=11069, win 20%, med -0.54%, dd 116,158) |
| 5m x8 | hold 60m | -108,041 (n=9902, win 30%, med -0.59%, dd 108,085) | -56,074 (n=5169, win 30%, med -0.58%, dd 56,158) |
| 5m x8 | hold 4h | -36,300 (n=3064, win 37%, med -0.66%, dd 36,484) | -19,108 (n=1556, win 35%, med -0.68%, dd 19,386) |
| 5m x8 | hold 24h | -5,314 (n=569, win 40%, med -1.17%, dd 9,085) | -1,155 (n=282, win 40%, med -0.94%, dd 3,079) |
| 5m x8 | trail 10% / 24h | -1,618 (n=666, win 40%, med -1.17%, dd 5,551) | -3,343 (n=323, win 39%, med -1.57%, dd 5,012) |
| 5m x8 | trail 10% + stop 8% | -10,936 (n=680, win 37%, med -1.54%, dd 12,187) | -2,904 (n=336, win 38%, med -1.17%, dd 3,989) |
| 5m x15 | hold 15m | -154,360 (n=14712, win 24%, med -0.55%, dd 154,362) | -81,350 (n=7790, win 22%, med -0.55%, dd 81,350) |
| 5m x15 | hold 60m | -82,320 (n=8213, win 32%, med -0.58%, dd 82,368) | -48,265 (n=4329, win 31%, med -0.58%, dd 48,265) |
| 5m x15 | hold 4h | -28,281 (n=2822, win 36%, med -0.64%, dd 29,244) | -14,432 (n=1452, win 37%, med -0.64%, dd 14,928) |
| 5m x15 | hold 24h | -5,221 (n=558, win 42%, med -0.96%, dd 7,481) | -2,241 (n=277, win 41%, med -1.07%, dd 5,679) |
| 5m x15 | trail 10% / 24h | -10,803 (n=678, win 40%, med -1.37%, dd 11,667) | -2,417 (n=326, win 40%, med -1.11%, dd 3,008) |
| 5m x15 | trail 10% + stop 8% | -9,043 (n=686, win 39%, med -1.49%, dd 9,362) | -462 (n=342, win 42%, med -1.09%, dd 2,818) |
| 5m x30 | hold 15m | -83,956 (n=8309, win 25%, med -0.54%, dd 83,956) | -41,773 (n=3977, win 24%, med -0.54%, dd 41,773) |
| 5m x30 | hold 60m | -56,220 (n=5596, win 33%, med -0.59%, dd 56,246) | -31,251 (n=2754, win 33%, med -0.61%, dd 31,435) |
| 5m x30 | hold 4h | -24,119 (n=2308, win 37%, med -0.65%, dd 24,695) | -17,895 (n=1141, win 35%, med -0.82%, dd 18,263) |
| 5m x30 | hold 24h | -10,343 (n=533, win 40%, med -1.20%, dd 11,519) | -9,765 (n=262, win 39%, med -1.46%, dd 9,765) |
| 5m x30 | trail 10% / 24h | -5,596 (n=662, win 40%, med -1.33%, dd 6,175) | -6,233 (n=339, win 37%, med -1.63%, dd 6,457) |
| 5m x30 | trail 10% + stop 8% | -8,250 (n=677, win 38%, med -1.56%, dd 9,004) | -8,804 (n=346, win 35%, med -1.77%, dd 9,062) |

## Full €20k in one name (impact binds harder)

| trigger | exit | direction | IS | OOS |
| --- | --- | --- | --- | --- |
| clock open | hold 60m | any | -154,668 (n=841, win 24%, med -0.86%, dd 155,063) | -65,061 (n=393, win 19%, med -0.84%, dd 65,061) |
| clock open | hold 4h | any | -112,993 (n=603, win 31%, med -0.91%, dd 113,927) | -38,039 (n=289, win 31%, med -0.83%, dd 39,634) |
| clock open | trail 10% / 24h | any | -93,188 (n=520, win 38%, med -1.24%, dd 93,811) | -27,639 (n=250, win 38%, med -1.02%, dd 34,364) |
| clock open | trail 10% + stop 8% | any | -96,968 (n=525, win 38%, med -1.21%, dd 97,592) | -34,359 (n=252, win 38%, med -1.02%, dd 41,083) |
| 1m x20 | hold 60m | any | -1,711,389 (n=10488, win 23%, med -0.88%, dd 1,711,836) | -872,421 (n=5359, win 23%, med -0.84%, dd 872,565) |
| 1m x20 | hold 60m | up | -1,514,499 (n=9120, win 22%, med -0.88%, dd 1,515,062) | -796,270 (n=4669, win 20%, med -0.89%, dd 796,270) |
| 1m x20 | hold 4h | any | -492,911 (n=3150, win 33%, med -0.89%, dd 494,027) | -274,977 (n=1583, win 28%, med -0.99%, dd 278,610) |
| 1m x20 | hold 4h | up | -535,358 (n=3018, win 32%, med -0.95%, dd 536,625) | -217,714 (n=1510, win 30%, med -0.91%, dd 217,836) |
| 1m x20 | trail 10% / 24h | any | -115,658 (n=658, win 36%, med -1.65%, dd 134,286) | -63,069 (n=312, win 39%, med -1.44%, dd 66,064) |
| 1m x20 | trail 10% / 24h | up | -105,585 (n=636, win 40%, med -1.29%, dd 110,039) | -45,407 (n=319, win 38%, med -1.24%, dd 59,435) |
| 1m x20 | trail 10% + stop 8% | any | -74,826 (n=685, win 38%, med -1.48%, dd 109,450) | -50,362 (n=323, win 40%, med -1.22%, dd 56,394) |
| 1m x20 | trail 10% + stop 8% | up | -142,286 (n=658, win 37%, med -1.62%, dd 142,978) | -61,202 (n=327, win 39%, med -1.31%, dd 71,356) |
| 1m x40 | hold 60m | any | -1,539,066 (n=9138, win 24%, med -0.89%, dd 1,539,755) | -779,050 (n=4668, win 23%, med -0.85%, dd 779,050) |
| 1m x40 | hold 60m | up | -1,198,245 (n=7421, win 24%, med -0.89%, dd 1,198,767) | -653,269 (n=3705, win 22%, med -0.90%, dd 653,269) |
| 1m x40 | hold 4h | any | -507,079 (n=2986, win 32%, med -0.95%, dd 511,947) | -262,791 (n=1506, win 30%, med -1.00%, dd 265,870) |
| 1m x40 | hold 4h | up | -434,262 (n=2767, win 32%, med -0.96%, dd 436,241) | -268,974 (n=1386, win 29%, med -1.06%, dd 271,750) |
| 1m x40 | trail 10% / 24h | any | -116,733 (n=657, win 38%, med -1.38%, dd 117,348) | -60,090 (n=319, win 39%, med -1.05%, dd 69,031) |
| 1m x40 | trail 10% / 24h | up | -136,132 (n=644, win 36%, med -1.47%, dd 147,784) | -66,591 (n=313, win 34%, med -1.73%, dd 79,760) |
| 1m x40 | trail 10% + stop 8% | any | -160,189 (n=674, win 36%, med -1.74%, dd 160,804) | -40,353 (n=329, win 38%, med -1.25%, dd 57,613) |
| 1m x40 | trail 10% + stop 8% | up | -161,804 (n=658, win 37%, med -1.67%, dd 162,419) | -47,403 (n=329, win 36%, med -1.50%, dd 64,065) |
| 5m x15 | hold 60m | any | -1,310,875 (n=8213, win 25%, med -0.88%, dd 1,311,226) | -741,666 (n=4329, win 25%, med -0.88%, dd 741,666) |
| 5m x15 | hold 60m | up | -1,005,904 (n=6353, win 25%, med -0.88%, dd 1,006,082) | -571,077 (n=3243, win 23%, med -0.92%, dd 571,077) |
| 5m x15 | hold 4h | any | -450,482 (n=2822, win 32%, med -0.93%, dd 457,459) | -230,954 (n=1452, win 32%, med -0.94%, dd 233,817) |
| 5m x15 | hold 4h | up | -398,358 (n=2548, win 32%, med -0.96%, dd 401,478) | -223,603 (n=1288, win 31%, med -1.00%, dd 225,906) |
| 5m x15 | trail 10% / 24h | any | -148,279 (n=678, win 38%, med -1.67%, dd 154,183) | -43,478 (n=326, win 37%, med -1.42%, dd 45,004) |
| 5m x15 | trail 10% / 24h | up | -127,781 (n=656, win 37%, med -1.86%, dd 134,267) | -50,610 (n=340, win 40%, med -1.17%, dd 55,784) |
| 5m x15 | trail 10% + stop 8% | any | -131,025 (n=686, win 37%, med -1.80%, dd 131,332) | -24,902 (n=342, win 39%, med -1.41%, dd 34,162) |
| 5m x15 | trail 10% + stop 8% | up | -149,318 (n=677, win 36%, med -1.84%, dd 151,044) | -28,309 (n=336, win 42%, med -0.94%, dd 34,136) |

## Cells positive in both IS and OOS (€2k, up-only, n large enough)

None on the €200k–€300k section.

Neighbour bands (€150–250k, €250–400k) are not in this replay. On daily bars
those bands already had no close-to-close edge (unusual-volume lab). This run
spends the minute data on the section that was actually asked.


Gross PnL (before costs) is in the JSON under `gross_pnl`. A cell that is only
positive after a tiny sample, or only in one neighbour band, is not an edge.

Reproduce:

```bash
.venv/bin/python -m bot.research.volume_velocity --fetch
.venv/bin/python -m bot.research.volume_velocity
```
