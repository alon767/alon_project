# -*- coding: utf-8 -*-
"""PermitAgent - the full run, shared by the button "הרצה מלאה" and the continuous mode "עבודה רציפה".

For every plan: names (permit_write), levels (permit_levels), then the other agents' stages when their
modules exist (permit_roof_basement / permit_dims / permit_elev). After every plan a PNG of the plan and
its check table (MD) are written to הערות\\auto - the agents read them there.

Continuous mode (Alon approved, 01.10.26): an agent drops a request into הערות\\auto\\queue
({"views": ["00", "01"], "by": "סוכן 1", "note": "..."}); Revit runs it at the next idle moment (newest
installed code, reloaded) and answers in הערות\\auto\\done with the same file name. Only this pipeline runs,
only on a working copy (_AGENT); a request carries plan names, never code.
"""
import io
import os
import json
import datetime
import traceback

from Autodesk.Revit.DB import (ElementId, FilteredElementCollector, ViewPlan, ImageExportOptions, ExportRange,
                               ZoomFitType, ImageResolution, ImageFileType)
from System.Collections.Generic import List

import permit_write
import permit_levels
import permit_rooms

AUTO_DIR = os.path.join(os.environ['USERPROFILE'], u'OneDrive - אלון מיכאלי - אדריכל', u'ספריית ניסיונות',
                        u'עריכת בקשה להיתר', u'הערות', u'auto')
QUEUE_DIR = os.path.join(AUTO_DIR, u'queue')
DONE_DIR = os.path.join(AUTO_DIR, u'done')
STATUS_FILE = os.path.join(AUTO_DIR, u'watch_status.json')
# reloaded before every continuous run, in dependency order (the newest installed code is used)
RELOAD_ORDER = (u'permit_rooms', u'permit_write', u'permit_apts', u'permit_levels', u'permit_ground',
                u'permit_roof_basement', u'permit_dims', u'permit_elev', u'permit_fullrun')
OPTIONAL = ((u'permit_roof_basement', u'run_roof_basement_view', u'מרתף / גג'),
            (u'permit_dims', u'run_dims', u'מידות'),
            (u'permit_elev', u'run_elev_for_plan', u'חזיתות / חתכים'))


def _dirs():
    for d in (AUTO_DIR, QUEUE_DIR, DONE_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)


def template_name(doc, v):
    t = doc.GetElement(v.ViewTemplateId) if v.ViewTemplateId != ElementId.InvalidElementId else None
    return permit_rooms.elem_name(t) if t is not None else u''


def permit_plans(doc):
    """the permit plans: plan views (floor / area plans) whose view template is the permit one"""
    plans = [v for v in FilteredElementCollector(doc).OfClass(ViewPlan)
             if not v.IsTemplate and v.GenLevel is not None and u'להיתר' in template_name(doc, v)]
    plans.sort(key=lambda v: (v.GenLevel.ProjectElevation, v.Name))
    return plans


def optional_stages(log):
    out = []
    for mod, fn, title in OPTIONAL:
        try:
            m = __import__(mod)
            f = getattr(m, fn, None)
            if f is not None:
                out.append((title, f))
        except ImportError:
            pass
        except Exception as ex:
            log(u'stage %s not loaded: %s' % (mod, ex))
    return out


def export_png(doc, v, stamp):
    """a picture of the plan as printed (crop region, annotations) - read-only, no transaction"""
    base = os.path.join(AUTO_DIR, u'tmp_%s' % stamp)
    o = ImageExportOptions()
    o.ExportRange = ExportRange.SetOfViews
    o.SetViewsAndSheets(List[ElementId]([v.Id]))
    o.FilePath = base
    o.ZoomType = ZoomFitType.FitToPage
    o.PixelSize = 5000
    o.ImageResolution = ImageResolution.DPI_300
    o.HLRandWFViewsFileType = ImageFileType.PNG
    o.ShadowViewsFileType = ImageFileType.PNG
    doc.ExportImage(o)
    target = None
    for f in [f for f in os.listdir(AUTO_DIR) if f.startswith(u'tmp_%s' % stamp)]:
        target = os.path.join(AUTO_DIR, u'%s_%s.png' % (stamp, v.Name))
        if os.path.exists(target):
            os.remove(target)
        os.rename(os.path.join(AUTO_DIR, f), target)
    return target


def run_plans(doc, uidoc, views, log, choose_name, stamp=None):
    """the whole pipeline on every plan of `views`. -> [(view name, notes, rows, png, md)]"""
    _dirs()
    stamp = stamp or datetime.datetime.now().strftime('%y%m%d_%H%M%S')
    summary = []
    for v in views:
        rows, notes = [], []
        log(u'--- full run: %s' % v.Name)
        try:
            w = permit_write.run_write(doc, uidoc, v, choose_name, log, lambda n: True)
            notes.append(u'חדרים: %d, תוויות: %d, דולגו: %d' % (w['created'], w['labels'], w['skipped']))
        except permit_write.Cancelled:
            notes.append(u'חדרים ושמות: בוטל')
            summary.append((v.Name, notes, rows, None, None))
            continue
        except Exception as ex:
            log(u'ERROR names %s\n%s' % (v.Name, traceback.format_exc()))
            notes.append(u'חדרים ושמות נכשל: %s' % ex)
        try:
            lv = permit_levels.run_levels(doc, v, log)
            rows += [(u'מפלסים',) + tuple(c) for c in lv.get('checks', [])]
            notes.append(u'סימוני מפלס: %d' % lv.get('spots', 0))
        except Exception as ex:
            log(u'ERROR levels %s\n%s' % (v.Name, traceback.format_exc()))
            notes.append(u'מפלסים נכשל: %s' % ex)
        for title, f in optional_stages(log):
            try:
                r = f(doc, v, log) or {}
                rows += [(title,) + tuple(c) for c in r.get('checks', [])]
            except Exception as ex:
                log(u'ERROR %s %s\n%s' % (title, v.Name, traceback.format_exc()))
                notes.append(u'%s נכשל: %s' % (title, ex))
        png = md = None
        try:
            png = export_png(doc, v, stamp)
        except Exception as ex:
            log(u'png export failed: %s' % ex)
            notes.append(u'שמירת תמונה נכשלה: %s' % ex)
        try:
            md = os.path.join(AUTO_DIR, u'%s_%s.md' % (stamp, v.Name))
            with io.open(md, 'w', encoding='utf-8') as fh:
                fh.write(u'# %s - %s\n\n%s\n\n%s\n\n' % (v.Name, stamp, permit_write.VERSION,
                                                         u'\n'.join(u'- ' + n for n in notes)))
                fh.write(u'| שלב | כלל | נמצאו | בוצעו | הערה |\n|---|---|---|---|---|\n')
                for r in rows:
                    fh.write(u'| %s |\n' % u' | '.join(u'%s' % x for x in r))
        except Exception as ex:
            log(u'check table not written: %s' % ex)
        summary.append((v.Name, notes, rows, png, md))
    return summary


# ------------------------------------------------------------------------------------- continuous mode
def write_status(state, extra=None):
    try:
        _dirs()
        d = {u'state': state, u'time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             u'version': permit_write.VERSION}
        if extra:
            d.update(extra)
        _dump(STATUS_FILE, d)
    except Exception:
        pass


def _dump(path, obj):
    s = json.dumps(obj, ensure_ascii=False, indent=1)
    if not isinstance(s, unicode):          # CPython 2 str (IronPython's str is already unicode)
        s = s.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as fh:
        fh.write(s)


def pending_requests():
    try:
        return sorted(f for f in os.listdir(QUEUE_DIR) if f.lower().endswith(u'.json'))
    except Exception:
        return []


def auto_name(r):
    """no dialogs in continuous mode: a space without a name from an earlier run gets the worker's own guess
    (or no room, when it has none) - reported in the answer"""
    g = r.get('guess')
    return g if g and g != u'?' else None


def process_one(uiapp, log):
    """run the oldest request of the queue. Called by the continuous mode in a valid API context."""
    reqs = pending_requests()
    if not reqs:
        return None
    name = reqs[0]
    src = os.path.join(QUEUE_DIR, name)
    answer = {u'request': name, u'started': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              u'version': permit_write.VERSION, u'results': [], u'errors': []}
    try:
        with io.open(src, 'r', encoding='utf-8-sig') as fh:
            req = json.loads(fh.read())
    except Exception as ex:
        req = {}
        answer[u'errors'].append(u'הבקשה לא נקראה: %s' % ex)
    try:
        os.remove(src)
    except Exception:
        pass
    answer[u'by'] = req.get(u'by', u'')
    answer[u'note'] = req.get(u'note', u'')
    uidoc = uiapp.ActiveUIDocument
    doc = uidoc.Document if uidoc is not None else None
    if doc is None or permit_write.WRITE_MARK not in (doc.Title or u''):
        answer[u'errors'].append(u'הקובץ הפעיל אינו עותק עבודה (_AGENT) - לא הורץ דבר')
    else:
        plans = permit_plans(doc)
        want = [w.strip() for w in (req.get(u'views') or []) if w and w.strip()]
        views = [v for v in plans if v.Name in want] if want else []
        missing = [w for w in want if w not in [v.Name for v in plans]]
        if missing:
            answer[u'errors'].append(u'תכניות לא נמצאו: %s' % u', '.join(missing))
        if views:
            write_status(u'running', {u'request': name})
            log(u'=== continuous run %s by %s: %s' % (name, answer[u'by'], u', '.join(v.Name for v in views)))
            for vname, notes, rows, png, md in run_plans(doc, uidoc, views, log, auto_name):
                answer[u'results'].append({u'view': vname, u'notes': notes, u'png': png, u'md': md})
    answer[u'finished'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        _dump(os.path.join(DONE_DIR, name), answer)
    except Exception as ex:
        log(u'answer not written: %s' % ex)
    write_status(u'waiting', {u'last': name})
    return name
