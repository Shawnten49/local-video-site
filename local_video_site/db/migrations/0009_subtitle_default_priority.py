from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


subtitle_default_priority_migration = Migration(
    BASELINE_VERSION + 8,
    "prefer bilingual subtitle tracks",
    (
        """
        UPDATE video_subtitle_assets
        SET default_track = 0
        WHERE lower(extension) = 'vtt'
        """,
        """
        UPDATE video_subtitle_assets
        SET default_track = 1
        WHERE id IN (
            SELECT MIN(id)
            FROM video_subtitle_assets
            WHERE lower(extension) = 'vtt'
              AND (
                  title LIKE '%中英%'
                  OR title LIKE '%中上英下%'
                  OR title LIKE '%中下英上%'
                  OR title LIKE '%简英%'
                  OR title LIKE '%繁英%'
                  OR title LIKE '%中英双%'
                  OR title LIKE '%双语%'
              )
            GROUP BY video_id
        )
        """,
        """
        UPDATE video_subtitle_assets
        SET default_track = 1
        WHERE id IN (
            SELECT MIN(a.id)
            FROM video_subtitle_assets a
            WHERE lower(a.extension) = 'vtt'
              AND (
                  a.title LIKE '%简体%'
                  OR a.title LIKE '%繁体%'
                  OR a.title LIKE '%中文%'
                  OR a.title LIKE '%官方字幕%'
                  OR lower(COALESCE(a.language, '')) IN (
                      'chi', 'zho', 'zh', 'zh-hans', 'zh-hant'
                  )
                  OR lower(COALESCE(a.language, '')) LIKE 'zh-%'
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM video_subtitle_assets selected
                  WHERE selected.video_id = a.video_id
                    AND lower(selected.extension) = 'vtt'
                    AND selected.default_track = 1
              )
            GROUP BY a.video_id
        )
        """,
        """
        UPDATE video_subtitle_assets
        SET default_track = 1
        WHERE id IN (
            SELECT MIN(a.id)
            FROM video_subtitle_assets a
            WHERE lower(a.extension) = 'vtt'
              AND NOT EXISTS (
                  SELECT 1
                  FROM video_subtitle_assets selected
                  WHERE selected.video_id = a.video_id
                    AND lower(selected.extension) = 'vtt'
                    AND selected.default_track = 1
              )
            GROUP BY a.video_id
        )
        """,
    ),
    compatible_checksums=(
        "78ea40cee7fbc16c0c14a54137c8523e60d6d1430db636d8d3cff38f307097bf",
    ),
)
