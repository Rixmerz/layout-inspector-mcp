from layout_inspector.geometry import Rect


def test_edges_and_area():
    r = Rect(10, 20, 100, 50)
    assert (r.right, r.bottom, r.area) == (110, 70, 5000)
    assert not r.is_empty


def test_touching_edges_do_not_intersect():
    assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 10, 10))
    assert not Rect(0, 0, 10, 10).intersects(Rect(0, 10, 10, 10))


def test_intersection_area_is_the_shared_box():
    assert Rect(0, 0, 100, 100).intersection_area(Rect(50, 50, 100, 100)) == 2500
    assert Rect(0, 0, 10, 10).intersection_area(Rect(50, 50, 10, 10)) == 0


def test_intersection_of_disjoint_rects_is_empty():
    assert Rect(0, 0, 10, 10).intersection(Rect(90, 90, 10, 10)).is_empty


def test_from_dict_round_trips():
    data = {"x": 1.5, "y": 2.5, "w": 3.0, "h": 4.0}
    assert Rect.from_dict(data).to_dict() == data
