#!/bin/sh
# Copies the frame classifier's inference code from the project it is trained in, so this repository runs without that
# project (AGENTS.md: "This repository never depends on another project's files"). Run it after each retraining whose
# code changed, beside copying the weights into ../weights/frame-classifier/. The copies stay byte for byte the same.
# Usage: ./copy_from_training_project.sh [path to frame-knowledge-classifier]
here=$(cd "$(dirname "$0")" && pwd)
source_dir=${1:-"$here/../../frame-knowledge-classifier"}
package="$source_dir/src/frame_knowledge_classifier"
[ -d "$package" ] || { echo "no classifier package at $package" >&2; exit 1; }
target="$here/src/frame_knowledge_classifier"
mkdir -p "$target/models" "$target/services"
for module in __init__.py config.py \
              models/__init__.py models/classifier_record.py models/cuts_record.py models/dataset_record.py \
              models/decision_record.py models/ink_parts_record.py models/label_vocabulary.py \
              models/tags_classifier_record.py models/task_record.py \
              services/__init__.py services/classifier_service.py services/dataset_service.py \
              services/embedding_service.py services/filter_service.py services/ink_rule_service.py \
              services/ink_service.py services/tags_service.py; do
  cp "$package/$module" "$target/$module" || exit 1
done
echo "copied the inference modules from $package"
