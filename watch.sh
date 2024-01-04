#!/bin/bash

# Check if the ADDRESS argument is provided
if [ -z "$1" ]; then
  echo "Please provide the ADDRESS as the first argument."
  exit 1
fi

# Assign the first argument to ADDRESS
ADDRESS="$1"

# Check if the FILEPATH argument is provided
if [ -z "$2" ]; then
  echo "Please provide the FILEPATH as the second argument."
  exit 1
fi

# Assign the second argument to FILEPATH
FILEPATH="$2"

# Check if the file exists
if [ ! -f "$FILEPATH" ]; then
  echo "File $FILEPATH does not exist."
  exit 1
fi


# Use entr to watch the file and execute the command when it changes
echo "$FILEPATH" | entr -s "CODE=\$(cat $FILEPATH); blitd tx script update-script --address $ADDRESS --code \"\$CODE\" --grantee $ADDRESS --from $ADDRESS --gas-adjustment 2 --gas auto -y --chain-id blit-dev-1"
