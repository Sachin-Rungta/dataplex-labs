"""Eval harness for the Business Glossary Agent.

Headless: runs the recommendation pipeline directly (bypassing the ADK
runner) and scores it against YAML golden sets with an LLM-as-Judge for
term equivalence and category coherence.
"""
