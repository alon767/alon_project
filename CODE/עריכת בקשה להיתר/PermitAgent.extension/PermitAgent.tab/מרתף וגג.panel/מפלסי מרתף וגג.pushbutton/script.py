# -*- coding: utf-8 -*-
"""מפלסים בתכנית מרתף או גג (פרק 3ג). מרתף: ערך מוחלט בכל קו שבירת שיפוע ברמפה, שיפוע עם חץ, וערך באזור השטוח. גג: ראש המעקה הבנוי בפינות ("מעקה בנוי") ופני הגג. רץ רק על עותק עבודה (_AGENT). Ctrl+Z אחד מבטל הכול."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== start roof / basement')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.UI import TaskDialog
    from pyrevit import revit, script
    import permit_write
    import permit_roof_basement as RB
    trace(u'imports ok, code version: %s | %s' % (permit_write.VERSION, RB.VERSION))

    doc = revit.doc
    view = revit.active_view
    title = u'מפלסי מרתף וגג'
    if getattr(view, 'GenLevel', None) is None:
        TaskDialog.Show(title, u'יש להריץ מתוך תכנית קומה (מרתף או גג).')
        script.exit()
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        TaskDialog.Show(title, u'הכלי רץ רק על עותק עבודה.\nשם הקובץ חייב להכיל "%s".' % permit_write.WRITE_MARK)
        script.exit()
    if RB.mode_of(doc, view) is None:
        TaskDialog.Show(title, u'התכנית הפעילה אינה מרתף (מפלס מתחת ל-0.50-) ואינה גג (שם הקומה או התצוגה מכיל "גג").')
        script.exit()
    try:
        res = RB.run_roof_basement_view(doc, view, trace)
    except Exception as ex:
        trace(u'ERROR\n' + traceback.format_exc())
        TaskDialog.Show(title, u'%s' % ex)
        script.exit()

    output = script.get_output()
    output.set_title(u'PermitAgent - ' + title)
    output.print_md(u'### %s: נוצרו %d ערכים | הוסרו מהרצה קודמת: %d' % (
        u'מרתף' if res['mode'] == u'basement' else u'גג', res.get('spots', 0), res.get('removed', 0)))
    rows = []
    for rule, found, done, note in res.get('checks', []):
        ok = u'✅' if (found == done or found == u'-') else u'⚠️'
        if rule.startswith(u'טקסט על טקסט'):
            ok = u'✅' if done == 0 else u'⚠️'
        rows.append([ok, rule, u'%s' % found, u'%s' % done, note])
    output.print_table(rows, columns=[u'', u'כלל', u'נמצאו', u'בוצעו', u'הערה'])
    output.print_md(u'_כל הפעולה היא Undo אחד - Ctrl+Z מבטל הכול._')
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
