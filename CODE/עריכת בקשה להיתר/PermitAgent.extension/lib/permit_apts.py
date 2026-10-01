# -*- coding: utf-8 -*-
"""PermitAgent - apartment numbers ("דירה X").

Office rule (Alon, 28.09.26):
  * numbers run continuously from the lowest residential floor upwards
  * on each floor: clockwise around the building centre, starting from the north-west (top-left)
  * a floor without its own permit plan continues the plan below it (typical floor): the typical plan
    shows its own number and, on a second line, the numbers of the same apartment on the floors above
  * the label sits inside the apartment, next to its entrance door
The other floors are analysed with temporary rooms that are always rolled back.
"""
import math

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInParameter, Level, ViewSheet, ElementId

import permit_rooms

START_ANGLE = 147.0   # degrees; just west of north-west (x right, y up) - fits floors 00, 01 and 08 of the example
LABEL_PREFIX = u"דירה מס' "     # office rule (Alon, 29.09.26): "דירה מס' 27"


def _is_story(level):
    p = level.get_Parameter(BuiltInParameter.LEVEL_IS_BUILDING_STORY)
    return p is None or p.AsInteger() == 1


def permit_views(doc, active_view):
    """plan views like the active one (same view template) that are placed on sheets -> {level id: view}"""
    placed = set()
    for sh in FilteredElementCollector(doc).OfClass(ViewSheet):
        for vid in sh.GetAllPlacedViews():
            placed.add(permit_rooms._id(vid))
    out = {}
    tpl = active_view.ViewTemplateId
    for v in FilteredElementCollector(doc).OfClass(type(active_view)):
        if v.IsTemplate or getattr(v, 'GenLevel', None) is None:
            continue
        if permit_rooms._id(v.Id) not in placed and v.Id != active_view.Id:
            continue
        if v.Id != active_view.Id and (tpl == ElementId.InvalidElementId or v.ViewTemplateId != tpl):
            continue
        out.setdefault(permit_rooms._id(v.GenLevel.Id), v)
    out[permit_rooms._id(active_view.GenLevel.Id)] = active_view
    return out


def apartments(rooms):
    """rooms (analysis dicts) -> [{'apt', 'center', 'entry'}] one per apartment"""
    by = {}
    for r in rooms:
        a = r.get('apartment')
        if a:
            by.setdefault(a, []).append(r)
    out = []
    for a, rs in by.items():
        pts = [r['label_pt'] for r in rs if r.get('label_pt')]
        if not pts:
            continue
        center = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]
        entry, entry_room, door = None, None, None
        for r in rs:
            if r.get('entry_label_pts'):
                entry, entry_room = r['entry_label_pts'][0], r
                door = (r.get('entry_doors') or [None])[0]
                break
        out.append({'apt': a, 'center': center, 'entry': entry or center, 'entry_room': entry_room,
                    'rooms': rs, 'door': door})
    return out


def clockwise(apts):
    """sort apartments clockwise around the floor centre, starting from the north-west"""
    if not apts:
        return apts
    cx = sum(a['center'][0] for a in apts) / len(apts)
    cy = sum(a['center'][1] for a in apts) / len(apts)

    def key(a):
        ang = math.degrees(math.atan2(a['center'][1] - cy, a['center'][0] - cx))
        return (START_ANGLE - ang) % 360.0
    return sorted(apts, key=key)


def number_labels(doc, active_view, active_rooms, log):
    """-> (labels for the active view [(text, [x, y] near the entrance, [room dicts of the apartment],
    entrance door {'loc', 'dir', 'reach'} or None)], summary lines)"""
    levels = sorted([l for l in FilteredElementCollector(doc).OfClass(Level) if _is_story(l)],
                    key=lambda l: l.ProjectElevation)
    pviews = permit_views(doc, active_view)
    active_level_id = permit_rooms._id(active_view.GenLevel.Id)

    # groups: a level with a permit plan starts a group; the levels above without one continue it
    groups = []
    for l in levels:
        lid = permit_rooms._id(l.Id)
        if lid in pviews:
            groups.append([l])
        elif groups:
            groups[-1].append(l)

    start = 1
    labels, summary = [], []
    for g in groups:
        head = g[0]
        hid = permit_rooms._id(head.Id)
        if hid == active_level_id:
            apts = apartments(active_rooms)
        else:
            log(u'מספור דירות: בודק את קומה %s' % head.Name)
            res = permit_rooms.run_dry(doc, pviews[hid], lambda m: None, detailed=False)
            apts = apartments(res['rooms'])
        if not apts:
            continue
        apts = clockwise(apts)
        n = len(apts)
        names = u', '.join(l.Name for l in g)
        summary.append(u'קומות {}: {} דירות לקומה → דירות {}–{}'.format(names, n, start, start + n * len(g) - 1))
        if hid == active_level_id:
            for idx, a in enumerate(apts):
                nums = [start + k * n + idx for k in range(len(g))]
                text = LABEL_PREFIX + str(nums[0])
                if len(nums) > 1:
                    text += u'\r' + u','.join(str(x) for x in nums[1:])
                labels.append((text, a['entry'], a['rooms'], a.get('door')))
        start += n * len(g)
    return labels, summary
