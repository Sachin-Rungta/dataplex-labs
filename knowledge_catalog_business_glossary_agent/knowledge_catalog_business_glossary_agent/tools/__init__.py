"""Tools for the Business Glossary Agent."""

from .catalog_search import (
    clear_search_cache,
    knowledge_catalog_multi_search,
)
from .clustering import cluster_concepts
from .context_graph import build_context_graph, summarize_context_graph
from .documentai_ingest import documentai_status, extract_with_documentai
from .embeddings import (
    cache_size as embedding_cache_size,
    clear_cache as clear_embedding_cache,
    cosine_similarity,
    embed_one,
    embed_texts,
)
from .entry_links import (
    create_entry_link,
    delete_entry_link,
    list_entry_links_for_term,
)
from .gcs_ingest import list_gcs_documents, read_gcs_document
from .glossary_crud import (
    create_glossary,
    create_glossary_category,
    create_glossary_term,
    delete_glossary,
    delete_glossary_category,
    delete_glossary_term,
    get_glossary,
    list_glossaries,
    list_glossary_categories,
    list_glossary_terms,
    update_glossary_term,
)
from .glossary_state import (
    existing_link_targets_for_terms,
    find_similar_existing_terms,
    find_similar_existing_terms_bulk,
    get_existing_glossary_state,
)
from .lineage import (
    entry_to_fqn,
    get_lineage_neighbors,
    lineage_status,
)
from .link_classifier import classify_relationships
from .ontology import score_term_candidates, suggest_link_candidates
from .semantic_ontology import (
    cluster_concepts_for_categories,
    embed_context_graph,
    score_term_candidates_semantic,
    suggest_link_candidates_bulk,
    suggest_link_candidates_semantic,
)
