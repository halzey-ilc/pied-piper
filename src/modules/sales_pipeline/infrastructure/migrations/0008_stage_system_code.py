"""System stages «Продажа» and «Встреча перенесена» in every pipeline (owner decision, 03.11.2026).

For each existing pipeline: the first won stage (or a won stage already named «Продажа»)
becomes the system «Продажа» — its id is kept, so deals and stage history stay valid — and
moves to the last position; if there is no won stage one is created. An open stage named
«Встреча перенесена» becomes the system one, otherwise it is added after the last open
stage. Ordinary stages that would clash with a system name get the suffix « (2)».
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import migrations, models

SALE = "sale"
MEETING_RESCHEDULED = "meeting_rescheduled"
NAMES = {SALE: "Продажа", MEETING_RESCHEDULED: "Встреча перенесена"}
SHIFT = 1000


def _add_system_stages(apps: Any, schema_editor: Any) -> None:
    db = schema_editor.connection.alias
    pipelines = apps.get_model("sales_pipeline", "PipelineRecord")
    stages_model = apps.get_model("sales_pipeline", "StageRecord")
    for pipeline in pipelines.objects.using(db).all():
        stages = list(stages_model.objects.using(db).filter(pipeline_id=pipeline.id).order_by("position"))
        if any(st.system_code == SALE for st in stages):
            continue
        lower = {code: name.lower() for code, name in NAMES.items()}
        sale = next((st for st in stages if st.kind == "won" and st.name.lower() == lower[SALE]), None)
        sale = sale or next((st for st in stages if st.kind == "won"), None)
        others = [st for st in stages if sale is None or st.id != sale.id]
        for st in others:
            if st.name.lower() == lower[SALE]:
                st.name = f"{st.name} (2)"
        rescheduled = next(
            (st for st in others if st.kind == "open" and st.name.lower() == lower[MEETING_RESCHEDULED]),
            None,
        )
        new_rescheduled = None
        if rescheduled is not None:
            rescheduled.system_code = MEETING_RESCHEDULED
        else:
            for st in others:
                if st.name.lower() == lower[MEETING_RESCHEDULED]:
                    st.name = f"{st.name} (2)"
            new_rescheduled = stages_model(
                id=uuid.uuid4(),
                pipeline_id=pipeline.id,
                name=NAMES[MEETING_RESCHEDULED],
                position=SHIFT * 3,
                probability=0,
                kind="open",
                is_active=True,
                system_code=MEETING_RESCHEDULED,
            )
            closing = next((i for i, st in enumerate(others) if st.kind != "open"), len(others))
            others.insert(closing, new_rescheduled)
        if sale is None:
            sale = stages_model(
                id=uuid.uuid4(),
                pipeline_id=pipeline.id,
                name=NAMES[SALE],
                position=SHIFT * 3 + 1,
                probability=100,
                kind="won",
                is_active=True,
            )
            new_sale = True
        else:
            new_sale = False
        sale.name = NAMES[SALE]
        sale.kind = "won"
        sale.is_active = True
        sale.system_code = SALE
        ordered = [*others, sale]
        # Positions and names are unique per pipeline: move existing rows out of the way first
        existing = [st for st in ordered if st is not new_rescheduled and not (st is sale and new_sale)]
        for index, st in enumerate(existing):
            stages_model.objects.using(db).filter(id=st.id).update(
                position=SHIFT + index, name=f"tmp-{st.id}"
            )
        for st in ordered:
            if st is new_rescheduled or (st is sale and new_sale):
                st.save(using=db, force_insert=True)
        for index, st in enumerate(ordered):
            stages_model.objects.using(db).filter(id=st.id).update(
                position=index, name=st.name, kind=st.kind, is_active=st.is_active, system_code=st.system_code
            )


class Migration(migrations.Migration):
    dependencies = [
        ("sales_pipeline", "0007_pipeline_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="stagerecord",
            name="system_code",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.RunPython(_add_system_stages, migrations.RunPython.noop),
    ]
