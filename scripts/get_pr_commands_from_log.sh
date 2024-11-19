#!/bin/bash

LOG_FILE=$1
PR_NUMBER=$2
cat $LOG_FILE | grep "$PR_NUMBER" | grep "Running" | awk -F": Running '" '{print $2}' | awk -F"' command" '{print $1}'
