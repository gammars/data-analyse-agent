import json

from app.agent.runtime import _try_build_chart_events


def test_python_analysis_figures_emit_chart_events() -> None:
    result = json.dumps(
        {
            "run_id": "run-1",
            "figures": [
                {
                    "chart_id": "chart-1",
                    "chart_url": "/charts/chart-1.png",
                    "title": "相关性热力图",
                }
            ],
            "result": {
                "figures": [
                    {
                        "chart_id": "chart-1",
                        "chart_url": "/charts/chart-1.png",
                        "title": "相关性热力图",
                    }
                ]
            },
        },
        ensure_ascii=False,
    )

    events = _try_build_chart_events(result)

    assert events == [
        {
            "type": "chart",
            "chart_id": "chart-1",
            "chart_type": "python",
            "title": "相关性热力图",
            "chart_url": "/charts/chart-1.png",
        }
    ]
