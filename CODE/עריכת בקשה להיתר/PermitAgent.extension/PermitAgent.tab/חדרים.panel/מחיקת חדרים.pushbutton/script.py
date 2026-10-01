# -*- coding: utf-8 -*-
"""מוחק את החדרים (Rooms) שהעובד יצר בקומות שבוחרים - לבד או יחד עם תוויות הטקסט שלו. חדרים וטקסטים שלא העובד יצר לא נוגעים. Ctrl+Z מבטל."""
import io
import os
import sys
import datetime
import traceback

TRACE = os.path.join(os.environ['APPDATA'], 'pyRevit', 'permit_write_trace.txt')


def trace(msg):
    with io.open(TRACE, 'a', encoding='utf-8') as f:
        f.write(u'%s  %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), msg))


trace(u'=== delete: start')
try:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib'))
    from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
    from pyrevit import revit, script, forms
    import permit_write

    doc = revit.doc
    TITLE = u'מחיקת חדרים'
    if permit_write.WRITE_MARK not in (doc.Title or u''):
        TaskDialog.Show(TITLE, u'הכלי רץ רק על עותק עבודה.\nשם הקובץ חייב להכיל "%s".' % permit_write.WRITE_MARK)
        script.exit()

    inv = permit_write.agent_inventory(doc)
    if not inv:
        TaskDialog.Show(TITLE, u'לא נמצאו חדרים או תוויות שהעובד יצר.')
        script.exit()

    # 1. which levels (checkboxes)
    items, by_item = [], {}
    for nm in sorted(inv):
        it = u'קומה {}  —  {} חדרים, {} תוויות'.format(nm, len(inv[nm]['rooms']), len(inv[nm]['labels']))
        items.append(it)
        by_item[it] = nm
    picked = forms.SelectFromList.show(items, title=u'מחיקת חדרים - סמן את הקומות',
                                       multiselect=True, button_name=u'המשך')
    if not picked:
        trace(u'delete: no level picked')
        script.exit()
    levels = [by_item[p] for p in picked]

    # 2. rooms only, or rooms + the agent's text labels
    ROOMS_ONLY = u'חדרים בלבד (התוויות נשארות)'
    WITH_LABELS = u'חדרים + תוויות הטקסט של העובד'
    CANCEL = u'ביטול'
    mode = forms.CommandSwitchWindow.show([ROOMS_ONLY, WITH_LABELS, CANCEL],
                                          message=u'מה למחוק בקומות {}?'.format(u', '.join(levels)))
    if mode not in (ROOMS_ONLY, WITH_LABELS):
        trace(u'delete: cancelled at mode')
        script.exit()
    with_labels = mode == WITH_LABELS

    n_rooms = sum(len(inv[l]['rooms']) for l in levels)
    n_labels = sum(len(inv[l]['labels']) for l in levels) if with_labels else 0
    ans = TaskDialog.Show(TITLE,
                          u'למחוק {} חדרים{} בקומות: {}?\n\n'
                          u'חדרים וטקסטים שלא העובד יצר לא יימחקו. אפשר לבטל ב-Ctrl+Z.'.format(
                              n_rooms, u' ו-{} תוויות טקסט'.format(n_labels) if with_labels else u'',
                              u', '.join(levels)),
                          TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No)
    if ans != TaskDialogResult.Yes:
        trace(u'delete: declined')
        script.exit()

    r, t = permit_write.delete_agent(doc, levels, with_labels)
    trace(u'delete: levels=%s with_labels=%s -> rooms %d, labels %d' % (levels, with_labels, r, t))
    TaskDialog.Show(TITLE, u'נמחקו {} חדרים{}.'.format(r, u' ו-{} תוויות'.format(t) if with_labels else u' (התוויות נשארו)'))
except SystemExit:
    raise
except Exception:
    trace(u'ERROR\n' + traceback.format_exc())
    raise
