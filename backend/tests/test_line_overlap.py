from app.core.line_overlap import overlap


def feat(fid, coords):
    return {"type": "Feature", "id": fid, "geometry": {"type": "LineString", "coordinates": coords}, "properties": {}}


# 0.001° of longitude at 37.5°N is ~88 m; 0.0001° of latitude is ~11 m
FS = feat("fs", [[-88.690, 37.510], [-88.680, 37.510]])                       # ~880 m east-west


def length_deg(piece):
    return abs(piece[-1][0] - piece[0][0])


def test_a_parallel_road_alongside_is_cut_entirely():
    [res] = overlap([feat("u", [[-88.690, 37.5101], [-88.680, 37.5101]])], [FS])
    assert res.pieces == [] and res.covered_m["fs"] > 800


def test_a_crossing_road_is_not_cut_at_the_crossing():
    [res] = overlap([feat("u", [[-88.685, 37.505], [-88.685, 37.515]])], [FS])
    assert res.pieces is None


def test_a_road_far_away_is_untouched():
    [res] = overlap([feat("u", [[-88.690, 37.520], [-88.680, 37.520]])], [FS])
    assert res.pieces is None and res.covered_m == {}


def test_partial_overlap_keeps_the_uncovered_remainder():
    # runs along the FS road, then carries on ~440 m east past its end
    [res] = overlap([feat("u", [[-88.688, 37.5101], [-88.675, 37.5101]])], [FS])
    assert len(res.pieces) == 1
    piece = res.pieces[0]
    assert abs(piece[0][0] - -88.680) < 0.0004 and piece[-1] == [-88.675, 37.5101]


def test_short_leftover_ends_are_dropped_as_slivers():
    # extends only ~20 m past the FS road's end
    [res] = overlap([feat("u", [[-88.690, 37.5101], [-88.67978, 37.5101]])], [FS])
    assert res.pieces == []


def test_a_short_gap_between_covered_stretches_is_smoothed_over():
    fs_with_gap = [feat("a", [[-88.690, 37.510], [-88.6852, 37.510]]), feat("b", [[-88.6848, 37.510], [-88.680, 37.510]])]
    [res] = overlap([feat("u", [[-88.690, 37.5101], [-88.680, 37.5101]])], fs_with_gap)
    assert res.pieces == []                     # the ~35 m gap doesn't leave a stub
