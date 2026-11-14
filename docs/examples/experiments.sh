# start workspace
demo_dir=$(mktemp -d)
export EXPNOTE_CONFIG_HOME="$demo_dir/config"
export EXPNOTE_DATA_HOME="$demo_dir/data"

expnote init --workspace training-demo
expnote moc add --workspace training-demo --moc-id control --title "Control experiments"
expnote topic add "Pendulum reproduction" --workspace training-demo --moc-id control
# end workspace

# start runs
expnote run add --workspace training-demo --moc-id control \
  --topic "Pendulum reproduction" --run-id sac00001 \
  --purpose "Check whether SAC reaches the target on Pendulum-v1" \
  --relation "Baseline for the PPO run" --status running \
  --meta algo=sac --meta env_id=Pendulum-v1 --meta-json seed=1

expnote run update sac00001 --workspace training-demo --status finished \
  --result "Mean return -168 over 10 evaluation episodes" \
  --append-analysis "Synthetic example. Use the same evaluation setup for PPO."

expnote run add --workspace training-demo --moc-id control \
  --topic "Pendulum reproduction" --run-id ppo00001 \
  --purpose "Evaluate PPO on Pendulum-v1 with the same protocol" \
  --relation "Compare with [[sac00001]]" --status finished \
  --result "Mean return -202 over 10 evaluation episodes" \
  --meta algo=ppo --meta env_id=Pendulum-v1 --meta-json seed=1

expnote run query --workspace training-demo --status finished \
  --where "metadata.env_id = 'Pendulum-v1'" --json
# end runs

# start benchmark
expnote benchmark add --workspace training-demo \
  --benchmark-id control-suite --title "Control reproduction"
expnote benchmark task add control-suite --workspace training-demo --title Pendulum-v1
expnote benchmark task add control-suite --workspace training-demo --title MountainCarContinuous-v0
expnote benchmark algo add control-suite --workspace training-demo --title SAC
expnote benchmark algo add control-suite --workspace training-demo --title PPO

expnote benchmark link control-suite sac00001 --workspace training-demo \
  --task Pendulum-v1 --algo SAC
expnote benchmark link control-suite ppo00001 --workspace training-demo \
  --task Pendulum-v1 --algo PPO
expnote benchmark matrix control-suite --workspace training-demo
# end benchmark
