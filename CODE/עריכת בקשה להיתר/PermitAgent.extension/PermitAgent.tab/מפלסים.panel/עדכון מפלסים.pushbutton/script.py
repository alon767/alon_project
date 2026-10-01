# -*- coding: utf-8 -*-
"""סימוני מפלס בקומת מגורים: מפלס בלובי הקומתי מול המעליות ("לובי קומתי" מעליו), מפלס בכל מרפסת ("מרפסת" מעליו), ובקומה טיפוסית בלוק "מפלסים:" ליד כותרת הקומה. רק על עותק עבודה (_AGENT). Ctrl+Z אחד מבטל הכול."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== start levels')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.UI import TaskDialog
    from pyrevit import revit, script
    import permit_write
    import permit_levels
    trace(u'imports ok, code version: %s' % permit_write.VERSION)

    doc = revit.doc
    view = revit.active_view
    if getattr(view, 'GenLevel', None) is None:
        TaskDialog.Show(u'עדכון מפלסים', u'יש להריץ מתוך תכנית קומה.')
        script.exit()
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        TaskDialog.Show(u'עדכון מפלסים',
                        u'הכלי רץ רק על עותק עבודה.\nשם הקובץ חייב להכיל "%s".' % permit_write.WRITE_MARK)
        script.exit()
    try:
        res = permit_levels.run_levels(doc, view, trace)
    except Exception as ex:
        trace(u'ERROR\n' + traceback.format_exc())
        TaskDialog.Show(u'עדכון מפלסים', u'%s' % ex)
        script.exit()

    trace(u'levels done: %s' % res)
    output = script.get_output()
    output.set_title(u'PermitAgent - עדכון מפלסים')
    output.print_md(u'### נוצרו {} סימוני מפלס (סוג: {})  |  הוסרו מהרצה קודמת: {}'.format(
        res['spots'], res['spot_type'] or u'ברירת מחדל - סוג PL_Base_Point לא נמצא', res.get('removed', 0)))
    output.print_md(u'- **לובי קומתי:** {}'.format(res['lobby'] or u'לא נוצר'))
    output.print_md(u'- **מרפסות:** {}'.format(res['balconies']))
    if 'ground_service' in res:
        output.print_md(u'- **קרקע:** {} חדרים גובלים (יחסי + אבסולוטי), {} סימונים אבסולוטיים ליד החדרים ובחצרות, '
                        u'{} סימונים אבסולוטיים באתר (קצוות ריצוף ושינויי גובה)'.format(
                            res.get('ground_service', 0), res.get('ground_paving', 0), res.get('ground_site', 0)))
    if res['block']:
        output.print_md(u'- **בלוק מפלסים (קומה טיפוסית):** {}'.format(res['block']))
    if res.get('checks'):
        output.print_md(u'### בדיקה מול ספר הכללים')
        rows = []
        for rule, found, done, note in res['checks']:
            ok = u'✅' if (found == done or found == u'-') and not (note and u'להריץ' in note) else u'⚠️'
            rows.append([ok, rule, u'%s' % found, u'%s' % done, note])
        output.print_table(rows, columns=[u'', u'כלל', u'נמצאו', u'בוצעו', u'הערה'])
    if res['errors']:
        output.print_md(u'⚠️ ' + u' | '.join(res['errors'][:8]))
    output.print_md(u'_כל הפעולה היא Undo אחד - Ctrl+Z מבטל הכול._')
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
