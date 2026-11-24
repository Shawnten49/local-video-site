from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


long_title_display_migration = Migration(
    BASELINE_VERSION + 5,
    "backfill readable display titles for 18AV marketing suffixes",
    (
        """
        UPDATE videos
        SET display_title_override = TRIM(
            CASE
                WHEN instr(title, ' 18AV在線') > 1
                 AND (
                    instr(title, ' 18AV在线') <= 0
                    OR instr(title, ' 18AV在線')
                       < instr(title, ' 18AV在线')
                 )
                THEN substr(title, 1, instr(title, ' 18AV在線') - 1)
                WHEN instr(title, ' 18AV在线') > 1
                THEN substr(title, 1, instr(title, ' 18AV在线') - 1)
                ELSE NULL
            END
        )
        WHERE COALESCE(display_title_override, '') = ''
          AND (
              instr(title, ' 18AV在線') > 1
              OR instr(title, ' 18AV在线') > 1
          )
          AND trim(
              CASE
                  WHEN instr(title, ' 18AV在線') > 1
                   AND (
                      instr(title, ' 18AV在线') <= 0
                      OR instr(title, ' 18AV在線')
                         < instr(title, ' 18AV在线')
                   )
                  THEN substr(title, 1, instr(title, ' 18AV在線') - 1)
                  WHEN instr(title, ' 18AV在线') > 1
                  THEN substr(title, 1, instr(title, ' 18AV在线') - 1)
                  ELSE ''
              END
          ) <> ''
        """,
        """
        UPDATE download_tasks
        SET display_title = (
            SELECT v.title
            FROM video_catalog v
            WHERE v.id = download_tasks.video_id
        )
        WHERE COALESCE(display_title, '') = ''
          AND video_id IS NOT NULL
          AND (
              instr(title, ' 18AV在線') > 1
              OR instr(title, ' 18AV在线') > 1
          )
          AND EXISTS (
              SELECT 1
              FROM video_catalog v
              WHERE v.id = download_tasks.video_id
                AND COALESCE(v.title, '') <> ''
          )
        """,
        """
        UPDATE download_tasks
        SET display_title = TRIM(
            CASE
                WHEN instr(title, ' 18AV在線') > 1
                 AND (
                    instr(title, ' 18AV在线') <= 0
                    OR instr(title, ' 18AV在線')
                       < instr(title, ' 18AV在线')
                 )
                THEN substr(title, 1, instr(title, ' 18AV在線') - 1)
                WHEN instr(title, ' 18AV在线') > 1
                THEN substr(title, 1, instr(title, ' 18AV在线') - 1)
                ELSE NULL
            END
        )
        WHERE COALESCE(display_title, '') = ''
          AND (
              instr(title, ' 18AV在線') > 1
              OR instr(title, ' 18AV在线') > 1
          )
        """,
    ),
)
