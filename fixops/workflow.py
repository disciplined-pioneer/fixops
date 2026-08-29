from typing import TypedDict, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from code_intel.indexer import ProjectIndexer
from code_intel.graph import GraphBuilder
from code_intel.error_analyzer import ErrorAnalyzer
from code_intel.context_builder import ContextBuilder
from code_intel.resolver import ProjectIndex, CallResolver

class FixOpsState(TypedDict):
    project_root: str
    error_log: dict
    logs_dir: str
    extra_ignore_dirs: tuple
    
    # Computed state
    modules: Any
    indexer: Any # Добавили поле
    index: Any
    graph: Any
    analysis_result: Dict
    llm_context: Any
    llm_prompt: str

# --- Nodes ---

async def indexer_node(state: FixOpsState):
    indexer = ProjectIndexer()
    ignore_dirs = ProjectIndexer.IGNORE_DIRS + state["extra_ignore_dirs"]
    modules = await indexer.scan(state["project_root"], ignore_dirs=ignore_dirs)
    return {"modules": modules, "indexer": indexer} # Возвращаем инстанс indexer

async def graph_builder_node(state: FixOpsState):
    idx = ProjectIndex(state["modules"])
    graph = await GraphBuilder(CallResolver(idx)).build(idx)
    return {"index": idx, "graph": graph}

async def error_analyzer_node(state: FixOpsState):
    analyzer = ErrorAnalyzer(state["index"], state["graph"])
    result = await analyzer.analyze_error(state["error_log"])
    return {"analysis_result": result}

async def context_builder_node(state: FixOpsState):
    ctx = await ContextBuilder(state["project_root"]).build_llm_context(state["index"], state["analysis_result"])
    prompt = ContextBuilder.render_llm_prompt(ctx)
    return {"llm_context": ctx, "llm_prompt": prompt}

# --- Router ---

def should_continue_to_context(state: FixOpsState):
    if state["analysis_result"].get("resolved_node") is None:
        return "end"
    return "build_context"

# --- Graph Definition ---

def create_workflow():
    workflow = StateGraph(FixOpsState)

    workflow.add_node("indexer", indexer_node)
    workflow.add_node("graph_builder", graph_builder_node)
    workflow.add_node("error_analyzer", error_analyzer_node)
    workflow.add_node("context_builder", context_builder_node)

    workflow.set_entry_point("indexer")
    workflow.add_edge("indexer", "graph_builder")
    workflow.add_edge("graph_builder", "error_analyzer")

    workflow.add_conditional_edges(
        "error_analyzer",
        should_continue_to_context,
        {
            "build_context": "context_builder",
            "end": END
        }
    )
    workflow.add_edge("context_builder", END)

    return workflow.compile()
