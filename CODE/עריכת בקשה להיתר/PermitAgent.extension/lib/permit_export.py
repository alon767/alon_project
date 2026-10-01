# -*- coding: utf-8 -*-
"""PermitAgent - read-only export of the model data needed to learn the permit-set "recipe".

Nothing here opens a Transaction; the model is only read.
Lengths are exported in cm, areas in m2, points as [x, y] in cm (model coordinates).
"""
import io
import json
import os
import datetime

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, BuiltInParameter, ElementId,
    View, ViewSheet, ViewType, Level, Dimension, SpotDimension, TextNote,
    IndependentTag, CurveElement, FamilyInstance, Viewport, StorageType,
    SpatialElement, Grid, Wall, DimensionType, CategoryType,
)
from Autodesk.Revit.DB.Architecture import RoomTag
from Autodesk.Revit.DB.Architecture import Room

FT_TO_CM = 30.48
SQFT_TO_M2 = 0.09290304

PLAN_TYPES = ('FloorPlan', 'AreaPlan', 'CeilingPlan')


# ---------- small helpers ----------

def _id(eid):
    if eid is None:
        return None
    v = getattr(eid, 'Value', None)
    if v is None:
        v = eid.IntegerValue
    return int(v)


def _cm(v):
    try:
        return round(v * FT_TO_CM, 1)
    except Exception:
        return None


def _pt(p):
    if p is None:
        return None
    return [_cm(p.X), _cm(p.Y)]


def _name(el):
    """Element.Name is unreliable for types in IronPython - fall back to parameters."""
    if el is None:
        return None
    try:
        n = el.Name
        if n:
            return n
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = el.get_Parameter(bip)
            if p and p.AsString():
                return p.AsString()
        except Exception:
            pass
    return None


def _type_name(doc, el):
    try:
        t = doc.GetElement(el.GetTypeId())
        if t is None:
            return None
        fam = getattr(t, 'FamilyName', None)
        nm = _name(t)
        if fam:
            return u'%s : %s' % (fam, nm)
        return nm
    except Exception:
        return None


def _cat(el):
    try:
        return el.Category.Name if el.Category else None
    except Exception:
        return None


def _param_values(el, only_with_value=True):
    out = {}
    try:
        params = el.Parameters
    except Exception:
        return out
    for p in params:
        try:
            nm = p.Definition.Name
            st = p.StorageType
            if st == StorageType.String:
                val = p.AsString()
            elif st == StorageType.ElementId:
                val = p.AsValueString()
            else:
                val = p.AsValueString()
            if only_with_value and (val is None or val == ''):
                continue
            out[nm] = val
        except Exception:
            continue
    return out


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------- sections of the export ----------

def export_project(doc):
    pi = doc.ProjectInformation
    return {
        'title': doc.Title,
        'path': doc.PathName,
        'revit_version': doc.Application.VersionNumber,
        'exported_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'project_information': _param_values(pi),
    }


def export_levels(doc):
    out = []
    for lv in FilteredElementCollector(doc).OfClass(Level):
        out.append({'id': _id(lv.Id), 'name': lv.Name, 'elevation_cm': _cm(lv.Elevation)})
    out.sort(key=lambda x: x['elevation_cm'])
    return out


def export_grids(doc):
    return [{'id': _id(g.Id), 'name': g.Name} for g in FilteredElementCollector(doc).OfClass(Grid)]


def _hidden_categories(doc, view):
    hidden = []
    for c in doc.Settings.Categories:
        try:
            if view.GetCategoryHidden(c.Id):
                hidden.append(c.Name)
        except Exception:
            pass
    return sorted(hidden)


def _hidden_subcategories(doc, view, parent_names):
    hidden = []
    for c in doc.Settings.Categories:
        if c.Name not in parent_names:
            continue
        for sc in c.SubCategories:
            try:
                if view.GetCategoryHidden(sc.Id):
                    hidden.append(u'%s / %s' % (c.Name, sc.Name))
            except Exception:
                pass
    return sorted(hidden)


def export_view_templates(doc):
    out = []
    for v in FilteredElementCollector(doc).OfClass(View):
        if not v.IsTemplate:
            continue
        item = {
            'id': _id(v.Id),
            'name': v.Name,
            'view_type': str(v.ViewType),
            'scale': _safe(lambda: v.Scale),
            'detail_level': _safe(lambda: str(v.DetailLevel)),
            'filters': [_name(doc.GetElement(fid)) for fid in _safe(lambda: v.GetFilters(), [])],
        }
        # full visibility detail only for plan templates (the ones the permit set uses)
        if str(v.ViewType) in PLAN_TYPES:
            item['hidden_categories'] = _hidden_categories(doc, v)
            item['hidden_line_subcategories'] = _hidden_subcategories(doc, v, (u'Lines', u'קווים'))
        out.append(item)
    out.sort(key=lambda x: x['name'])
    return out


def export_sheets(doc):
    out = []
    for sh in FilteredElementCollector(doc).OfClass(ViewSheet):
        tbs = FilteredElementCollector(doc, sh.Id).OfCategory(BuiltInCategory.OST_TitleBlocks).ToElements()
        vps = []
        for vpid in sh.GetAllViewports():
            vp = doc.GetElement(vpid)
            view = doc.GetElement(vp.ViewId)
            outline = _safe(lambda: vp.GetBoxOutline())
            vps.append({
                'view_id': _id(vp.ViewId),
                'view_name': _name(view),
                'view_type': str(view.ViewType) if view else None,
                'center_cm': _pt(_safe(lambda: vp.GetBoxCenter())),
                'box_min_cm': _pt(outline.MinimumPoint) if outline else None,
                'box_max_cm': _pt(outline.MaximumPoint) if outline else None,
                'viewport_type': _type_name(doc, vp),
            })
        out.append({
            'id': _id(sh.Id),
            'number': sh.SheetNumber,
            'name': sh.Name,
            'title_blocks': [_type_name(doc, tb) for tb in tbs],
            'viewports': vps,
            'params': _param_values(sh),
        })
    out.sort(key=lambda x: x['number'])
    return out


def _views_on_sheets(doc):
    placed = {}
    for sh in FilteredElementCollector(doc).OfClass(ViewSheet):
        for vid in sh.GetAllPlacedViews():
            placed[_id(vid)] = sh.SheetNumber
    return placed


def export_views(doc, placed):
    out = []
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate or isinstance(v, ViewSheet):
            continue
        tpl = doc.GetElement(v.ViewTemplateId) if v.ViewTemplateId != ElementId.InvalidElementId else None
        lvl = _safe(lambda: v.GenLevel)
        out.append({
            'id': _id(v.Id),
            'name': v.Name,
            'view_type': str(v.ViewType),
            'level': lvl.Name if lvl else None,
            'scale': _safe(lambda: v.Scale),
            'template': _name(tpl),
            'on_sheet': placed.get(_id(v.Id)),
            'type': _type_name(doc, v),
        })
    out.sort(key=lambda x: (x['view_type'], x['name']))
    return out


def _dimension_info(doc, d):
    info = {
        'id': _id(d.Id),
        'type': _type_name(doc, d),
        'segments': d.NumberOfSegments,
    }
    line = _safe(lambda: d.Curve)
    if line is not None:
        direction = _safe(lambda: line.Direction)
        if direction is not None:
            info['direction'] = [round(direction.X, 3), round(direction.Y, 3)]
        info['origin_cm'] = _pt(_safe(lambda: line.Origin))
    if d.NumberOfSegments > 0:
        vals = []
        for s in d.Segments:
            vals.append(_cm(s.Value) if s.Value is not None else None)
        info['values_cm'] = vals
        texts = []
        for s in d.Segments:
            t = u'%s|%s|%s' % (s.Prefix or u'', s.ValueOverride or u'', s.Suffix or u'')
            if s.Above or s.Below:
                t += u'|above=%s|below=%s' % (s.Above or u'', s.Below or u'')
            texts.append(t)
        if any(x != u'||' for x in texts):
            info['segment_texts'] = texts
    else:
        info['values_cm'] = [_cm(d.Value) if d.Value is not None else None]
        extra = u'%s|%s|%s' % (d.Prefix or u'', d.ValueOverride or u'', d.Suffix or u'')
        if d.Above or d.Below:
            extra += u'|above=%s|below=%s' % (d.Above or u'', d.Below or u'')
        if extra != u'||':
            info['segment_texts'] = [extra]
    refcats = {}
    try:
        for r in d.References:
            el = doc.GetElement(r.ElementId)
            c = _cat(el) or (el.GetType().Name if el is not None else u'?')
            refcats[c] = refcats.get(c, 0) + 1
    except Exception:
        pass
    info['reference_categories'] = refcats
    return info


def _tag_info(doc, t):
    info = {'id': _id(t.Id), 'type': _type_name(doc, t), 'head_cm': _pt(_safe(lambda: t.TagHeadPosition)),
            'has_leader': _safe(lambda: t.HasLeader), 'text': _safe(lambda: t.TagText)}
    try:
        ids = t.GetTaggedLocalElementIds()
        cats = []
        for eid in ids:
            cats.append(_cat(doc.GetElement(eid)))
        info['tagged_categories'] = cats
    except Exception:
        pass
    return info


def export_view_annotations(doc, view):
    vid = view.Id
    res = {'view_id': _id(vid), 'view_name': view.Name, 'counts_by_category': {}}

    all_els = FilteredElementCollector(doc, vid).WhereElementIsNotElementType().ToElements()
    counts = {}
    for el in all_els:
        if el.OwnerViewId != vid:
            continue  # only view-specific (annotation) elements
        c = _cat(el) or el.GetType().Name
        counts[c] = counts.get(c, 0) + 1
    res['counts_by_category'] = counts

    dims, spots, tags, room_tags, texts, lines, symbols = [], [], [], [], [], [], []
    for el in all_els:
        if el.OwnerViewId != vid:
            continue
        try:
            if isinstance(el, SpotDimension):
                spots.append({'id': _id(el.Id), 'type': _type_name(doc, el),
                              'value': _safe(lambda: el.ValueString),
                              'origin_cm': _pt(_safe(lambda: el.Origin)),
                              'ref_categories': [_cat(doc.GetElement(r.ElementId)) for r in el.References]})
            elif isinstance(el, Dimension):
                dims.append(_dimension_info(doc, el))
            elif isinstance(el, RoomTag):
                room = el.Room
                room_tags.append({'id': _id(el.Id), 'type': _type_name(doc, el),
                                  'room_number': room.Number if room else None,
                                  'room_name': _safe(lambda: room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()) if room else None,
                                  'head_cm': _pt(_safe(lambda: el.TagHeadPosition))})
            elif isinstance(el, IndependentTag):
                tags.append(_tag_info(doc, el))
            elif isinstance(el, TextNote):
                texts.append({'id': _id(el.Id), 'type': _type_name(doc, el), 'text': el.Text,
                              'coord_cm': _pt(el.Coord)})
            elif isinstance(el, CurveElement):
                gs = _safe(lambda: el.LineStyle)
                crv = _safe(lambda: el.GeometryCurve)
                lines.append({'id': _id(el.Id), 'style': _name(gs), 'kind': el.GetType().Name,
                              'length_cm': _cm(crv.Length) if crv else None,
                              'start_cm': _pt(_safe(lambda: crv.GetEndPoint(0))) if crv else None,
                              'end_cm': _pt(_safe(lambda: crv.GetEndPoint(1))) if crv else None})
            elif isinstance(el, FamilyInstance):
                loc = _safe(lambda: el.Location.Point)
                symbols.append({'id': _id(el.Id), 'category': _cat(el), 'type': _type_name(doc, el),
                                'point_cm': _pt(loc), 'params': _param_values(el)})
        except Exception:
            continue
    res['dimensions'] = dims
    res['spot_dimensions'] = spots
    res['tags'] = tags
    res['room_tags'] = room_tags
    res['text_notes'] = texts
    res['detail_lines'] = lines
    res['annotation_symbols'] = symbols
    crop = _safe(lambda: view.CropBox)
    if crop is not None and _safe(lambda: view.CropBoxActive):
        res['crop_min_cm'] = _pt(crop.Min)
        res['crop_max_cm'] = _pt(crop.Max)
    return res


def export_rooms(doc):
    out = []
    for r in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        if not isinstance(r, Room):
            continue
        loc = _safe(lambda: r.Location.Point)
        out.append({
            'id': _id(r.Id),
            'number': r.Number,
            'name': _safe(lambda: r.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()),
            'level': r.Level.Name if r.Level else None,
            'area_m2': round(r.Area * SQFT_TO_M2, 2) if r.Area else 0,
            'placed': loc is not None,
            'point_cm': _pt(loc),
            'params': _param_values(r),
        })
    return out


def export_openings(doc):
    out = []
    for bic in (BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
        for fi in FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType():
            host = _safe(lambda: fi.Host)
            sym = fi.Symbol
            lvl = doc.GetElement(fi.LevelId) if fi.LevelId != ElementId.InvalidElementId else None
            out.append({
                'id': _id(fi.Id),
                'category': _cat(fi),
                'type': _type_name(doc, fi),
                'level': _name(lvl),
                'host_id': _id(host.Id) if host else None,
                'point_cm': _pt(_safe(lambda: fi.Location.Point)),
                'mark': _safe(lambda: fi.get_Parameter(BuiltInParameter.ALL_MODEL_MARK).AsString()),
                'params': _param_values(fi),
                'type_params': _param_values(sym),
            })
    return out


def export_walls_summary(doc):
    summary = {}
    for w in FilteredElementCollector(doc).OfClass(Wall):
        lvl = doc.GetElement(w.LevelId)
        key = u'%s || %s || %s' % (_name(lvl), _type_name(doc, w),
                                   _safe(lambda: str(w.WallType.Function)))
        summary[key] = summary.get(key, 0) + 1
    return [{'level || type || function': k, 'count': v} for k, v in sorted(summary.items())]


def export_type_catalog(doc):
    """Annotation types that exist in the template (what the robot is allowed to use)."""
    cat = {}
    cat['dimension_types'] = sorted(set(u'%s : %s' % (str(t.StyleType), _name(t))
                                        for t in FilteredElementCollector(doc).OfClass(DimensionType)))
    tag_like = {}
    for sym in FilteredElementCollector(doc).WhereElementIsElementType().ToElements():
        try:
            c = sym.Category
            if c is None or c.CategoryType != CategoryType.Annotation:
                continue
            fam = getattr(sym, 'FamilyName', None) or u''
            tag_like.setdefault(c.Name, []).append(u'%s : %s' % (fam, _name(sym)))
        except Exception:
            continue
    for k in tag_like:
        tag_like[k] = sorted(set(tag_like[k]))
    cat['annotation_types_by_category'] = tag_like
    styles = []
    lines_cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
    for sc in lines_cat.SubCategories:
        styles.append(sc.Name)
    cat['line_styles'] = sorted(styles)
    return cat


def export_area_schemes(doc):
    out = []
    for s in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_AreaSchemes):
        out.append({'id': _id(s.Id), 'name': _name(s)})
    areas = 0
    for a in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Areas).WhereElementIsNotElementType():
        areas += 1
    return {'schemes': out, 'area_count': areas}


# ---------- main ----------

def run_export(doc, out_dir, log=None):
    def say(msg):
        if log:
            log(msg)

    data = {}
    say(u'פרטי פרויקט...')
    data['project'] = export_project(doc)
    data['levels'] = export_levels(doc)
    data['grids'] = export_grids(doc)
    say(u'View Templates...')
    data['view_templates'] = export_view_templates(doc)
    say(u'גיליונות ומבטים...')
    placed = _views_on_sheets(doc)
    data['sheets'] = export_sheets(doc)
    data['views'] = export_views(doc, placed)
    say(u'סימונים במבטי תכנית שעל גיליונות...')
    anns = []
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate or _id(v.Id) not in placed:
            continue
        if str(v.ViewType) not in PLAN_TYPES:
            continue
        say(u'  ' + v.Name)
        anns.append(export_view_annotations(doc, v))
    data['plan_view_annotations'] = anns
    say(u'חדרים, פתחים, קירות...')
    data['rooms'] = export_rooms(doc)
    data['openings'] = export_openings(doc)
    data['walls_summary'] = export_walls_summary(doc)
    data['area_schemes'] = export_area_schemes(doc)
    say(u'קטלוג סוגים...')
    data['type_catalog'] = export_type_catalog(doc)

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    safe_title = u''.join(ch for ch in doc.Title if ch not in u'\\/:*?"<>|')
    path = os.path.join(out_dir, u'%s_%s.json' % (safe_title, stamp))
    with io.open(path, 'w', encoding='utf-8') as f:
        txt = json.dumps(data, ensure_ascii=False, indent=1)
        if not isinstance(txt, type(u'')):
            txt = txt.decode('utf-8')
        f.write(txt)
    return path, data
