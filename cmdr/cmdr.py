#!/usr/bin/env python
import click
import coloredlogs, logging
import yaml
import json
import subprocess
import os,binascii
import tempfile
import ipaddress
from time import sleep
from shutil import which
import sys
import base64

logger = logging.getLogger(__name__)
coloredlogs.install(fmt='%(name)s[%(process)d] %(levelname)s %(message)s', level='DEBUG', logger=logger)

@click.group()
@click.option('--debug', is_flag=True)
@click.option('-f', '--cmdrFile', default='cmdr-project.yaml')
@click.pass_context
def cli(ctx, debug, cmdrfile):
    # ensure that ctx.obj exists and is a dict (in case `cli()` is called
    # by means other than the `if` block below)
    ctx.ensure_object(dict)
    ctx.obj['DEBUG'] = debug
    
    with open(cmdrfile) as f:
      ctx.obj['CMDR_PROJ'] = yaml.load(f, Loader=yaml.FullLoader)

@click.command()
@click.pass_context
@click.option('-n', '--namespace', default='local', help='application namespace configuration')
@click.option("--skipRepos", is_flag=True)
def setup(ctx, namespace, skiprepos):
  check_dependencies()

  if 'helm_repos' in ctx.obj['CMDR_PROJ'] and skiprepos is False:
    update_helm_repos(ctx.obj['CMDR_PROJ']['helm_repos'])

  local_env_check()

  if 'kind' in ctx.obj['CMDR_PROJ']:
    create_cluster(ctx.obj['CMDR_PROJ']['kind'])
    install_metal(ctx)

  create_namespace(ctx, namespace)
  #install_maesh(ctx)
  install_traefik(ctx)

  for svc in ctx.obj['CMDR_PROJ']['services']:
    if 'ignoreSetup' in svc and svc['ignoreSetup']:
      continue

    generate_helm_deployment(ctx, svc, namespace)


@click.command()
@click.pass_context
def reinstall_traefik(ctx):
  local_env_check()

  delete_traefik(ctx)
  install_traefik(ctx)

@click.command()
@click.pass_context
def reload_traefik(ctx):
  install_traefik(ctx)

@click.command()
@click.pass_context
@click.option('-e', '--env', default='local', help='environment configuration')
@click.option('--destroy', is_flag=True)
def wipe(ctx, env, destroy):
  local_env_check()

  if 'kind' in ctx.obj['CMDR_PROJ'] and env == 'local':
    if destroy:
      wipe_cluster(ctx.obj['CMDR_PROJ']['kind'])
    else:
      for svc in ctx.obj['CMDR_PROJ']['services']:
          delete_helm_deployment(ctx, svc, env)
      delete_traefik(ctx)
      delete_metal(ctx)
      delete_namespace(ctx, env)

@click.command()
@click.pass_context
def update_helm(ctx):
  update_helm_repos()


@click.command()
@click.pass_context
@click.option('-s', '--service', required=True)
@click.option('-e', '--env', default='local')
@click.option('-t', '--tag', default=None)
def deploy(ctx, service, env, tag):
  local_env_check()

  # validate service
  for svcObj in ctx.obj['CMDR_PROJ']['services']:
    if service == svcObj['name']:
      svc = svcObj

  if svc == None:
    logger.critical("service `{0}` was not found in project manifest -- see readme".format(service))
    exit(1)

  if tag != None and tag in svc:
    svc['tag'] = tag

  generate_helm_deployment(ctx, svc, env)

def delete_traefik(ctx):
  cmd = ["helm", "delete", "traefik"]
  try:
    if ctx.obj['DEBUG']:
      cmd = cmd + [ "--dry-run" ]

    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    pass

def local_env_check():
  # check the context
  try:
    cmd = ["kubectl", "config", "current-context"]
    cur_context = subprocess.check_output(cmd)
    if 'kind' not in cur_context.decode('utf-8').rstrip():
      logger.critical("detected non-kind kubernetes context, exiting")
      exit(1)
  except subprocess.CalledProcessError as e:
    logger.warning("Unable to determine context. This could be because you don't have a local environment or something else. Pausing for 5 seconds for you to re-evaluate your decisions before moving on.")
    sleep(5)

def delete_helm_deployment(ctx, svc, env):
  logger.info("processing [{0}]".format(svc['name']))

  if 'forceNamespace' in svc:
    env = svc['forceNamespace']

  helmCmd = [
    "helm", "delete",
    "--namespace={0}".format(env),
    svc['name']
  ]

  run_helm_cmd(ctx, helmCmd)

def generate_helm_deployment(ctx, svc, env):
  logger.info("processing [{0}]".format(svc['name']))

  imageSetCmd = []
  tmpValueArg = ""

  if 'forceNamespace' in svc:
    env = svc['forceNamespace']

  if 'config' in svc and env in svc['config']:
    serviceConfig = svc['config'][env]

    # process template overrides
    fd, tmpValuePath = tempfile.mkstemp()
    try:
      with os.fdopen(fd, 'w+') as tmp:
        if 'canary' in serviceConfig:
          can = {
            "canary": serviceConfig['canary']
          }
          tmp.write(yaml.dump(can))
        if 'ingress' in serviceConfig:
          ing = {
            "ingress": serviceConfig['ingress']
          }
          tmp.write(yaml.dump(ing))
        if 'jaeger' in serviceConfig:
          jae = {
            'jaeger': serviceConfig['jaeger']
          }
          tmp.write(yaml.dump(jae))
        if 'resources' in serviceConfig:
          res = {
            "resources": serviceConfig['resources']
          }
          tmp.write(yaml.dump(res))
        if 'env' in serviceConfig:
          envr = {
            "env": serviceConfig['env']
          }
          tmp.write(yaml.dump(envr))
        if 'service' in serviceConfig:
          serv = {
            "service": serviceConfig['service']
          }
          tmp.write(yaml.dump(serv))
        if 'actuatorHealth' in serviceConfig:
          acth = {
            "actuatorHealth": serviceConfig['actuatorHealth']
          }
          tmp.write(yaml.dump(acth))
        if 'replicaCount' in serviceConfig:
          repl = {
            "replicaCount": serviceConfig['replicaCount']
          }
          tmp.write(yaml.dump(repl))
        if 'nodeSelector' in serviceConfig:
          ndsel = {
            "nodeSelector": serviceConfig['nodeSelector']
          }
          tmp.write(yaml.dump(ndsel))

        tmp.close()
        tmpValueArg = "--values={0}".format(tmpValuePath)
    except:
      # failed to do something
      logger.critical("failed to generate temporary value overrides")
      if tmpValuePath != None:
        os.remove(tmpValuePath)
      exit(1)
  elif 'config' in svc:
    logger.warning("skipping {0}, value configuration for env {1} not found".format(svc['name'], env))
    return

  nameChartSuffix = [ svc['name'], svc['chart'] ]
  helmCmd = [
    "helm", "upgrade", "--install", "--reset-values",
    "--namespace={0}".format(env),
    "--set=fullnameOverride={0}".format(svc['name']),
    "--values={0}".format(svc['values'])
  ]

  if tmpValueArg != "":
    helmCmd = helmCmd + [ tmpValueArg ]

  if 'serviceConfig' in locals():
    if env == 'local' and 'retag' in serviceConfig and serviceConfig['retag'] == True:
      reTag = kind_load_image(ctx, svc['image'], svc['tag'])
      imageSetCmd = [
        "--set=image.repository={0}".format(svc['image']),
        "--set=image.tag={0}".format(reTag)
        ]
    elif 'tag' in svc:
      imageSetCmd = [
        "--set=image.repository={0}".format(svc['image']),
        "--set=image.tag={0}".format(svc['tag']),
        "--set=image.pullPolicy=Always"
      ]

  if ctx.obj['DEBUG']:
    helmCmd = helmCmd + [ "--debug", "--dry-run" ]

  run_helm_cmd(ctx, helmCmd + imageSetCmd + nameChartSuffix)

  if 'tmpValuePath' in locals():
    os.remove(tmpValuePath)

def kind_load_image(ctx, image, tag):
  if 'kind' not in ctx.obj['CMDR_PROJ']:
    logger.warning("kind config missing -- cannot push images")
    return

  reTag = binascii.b2a_hex(os.urandom(15)).decode("utf-8")
  
  docker_tag_cmd = ["docker", "tag", "{0}:{1}".format(image, tag), "{0}:{1}".format(image, reTag)]
  try:
    logger.debug('executing: ' + ' '.join(docker_tag_cmd))
    subprocess.check_call(docker_tag_cmd)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass
  
  if "load" in ctx.obj['CMDR_PROJ']['kind']:
    targets = ','.join(ctx.obj['CMDR_PROJ']['kind']['load'])

  cmd = ["kind", "load", "docker-image", "--name", ctx.obj['CMDR_PROJ']['kind']['name'], "{0}:{1}".format(image, reTag)]
  
  if 'targets' in locals():
    cmd.insert(3, "--nodes")
    cmd.insert(4, targets)

  try:
    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass

  return reTag

def update_helm_repos(repos):
  for repo in repos:
    command = ["helm", "repo", "add", repo['name'], repo['url']]
    try:
      
      logger.debug('executing: ' + ' '.join(command))
      subprocess.check_call(command)
    except subprocess.CalledProcessError as e:
      logger.error(e.output)
      pass
  
  command = ["helm", "repo", "update"]
  try:
    logger.debug('executing: ' + ' '.join(command))
    subprocess.check_call(command)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass

def run_helm_cmd(ctx, helmCmd):
  try:
    logger.debug('executing: ' + ' '.join(helmCmd))
    subprocess.check_call(helmCmd)
    sleep(3)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass

def check_dependencies():
  dependencies = [
    {
      "cmd": "helm",
      "installed": False,
      "url": "https://github.com/helm/helm/releases",
      "version": "3.0.1"
    },
    {
      "cmd": "kind",
      "installed": False,
      "url": "https://github.com/kubernetes-sigs/kind/releases",
      "version": "0.8.0"
    },
    {
      "cmd": "kubectl",
      "installed": False,
      "url": "https://v1-17.docs.kubernetes.io/docs/setup/release/notes/#client-binaries",
      "version": "1.17.4"
    },
    {
      "cmd": "docker",
      "installed": False,
      "url": "https://docs.docker.com/install/",
      "version": "19.03.8"
    }
  ]
  
  if os.getenv('CONTAINER', False):
    logger.info("skipping dependency check. We are in a container")
    return

  logger.info("checking dependencies - Note: supported versions are not checked")
  for dep in dependencies:
    logger.info("verifying {0} ({1})".format(dep["cmd"], dep["version"]))
    if not which(dep["cmd"]):
      output = "failed dependency check for `{0} ({1})` -- {2}".format(dep["cmd"], dep["version"], dep["url"])
      logger.critical(output)
      exit(1)

def delete_metal(ctx):
  if check_ns_exists('metallb-system') == False:
    logger.info("metallb already deleted, skipping")
    return

  commands = [
    ["kubectl", "delete", "-f", "https://raw.githubusercontent.com/google/metallb/v0.9.3/manifests/metallb.yaml"],
    ["kubectl", "delete", "-f", "https://raw.githubusercontent.com/google/metallb/v0.9.3/manifests/namespace.yaml"]
  ]

  for cmd in commands:
    try:
      if ctx.obj['DEBUG']:
        cmd = cmd + [ "--dry-run" ]
      logger.debug('executing: ' + ' '.join(cmd))
      subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
      pass

def install_metal(ctx):
  if check_ns_exists('metallb-system'):
    logger.notice("metallb already installed, skipping")
    return

  config = """apiVersion: v1
kind: ConfigMap
metadata:
  namespace: metallb-system
  name: config
data:
  config: |
    address-pools:
    - name: default
      protocol: layer2
      addresses:
      - {0}
"""
  try:
    cmd = ["docker", "network", "inspect", "kind", "-f", "{{ (index .IPAM.Config 0).Subnet }}"]
    logger.debug('executing: ' + ' '.join(cmd))
    output = subprocess.check_output(cmd).strip()
    n = ipaddress.IPv4Network(output.decode("utf-8"))
    first = n[-255]
    last = n[-2]
    ip_range = "{0}-{1}".format(first, last)
  except subprocess.CalledProcessError as e:
    logger.critical("failed to extract bridge subnet from kind network for metallb")
    exit(1)

  randKey=base64.b64encode(os.urandom(128))
  fd, valuePath = tempfile.mkstemp()

  commands = [    
    ["kubectl", "apply", "-f", "https://raw.githubusercontent.com/google/metallb/v0.9.3/manifests/namespace.yaml"],
    ["kubectl", "apply", "-f", "https://raw.githubusercontent.com/google/metallb/v0.9.3/manifests/metallb.yaml"],
    ["kubectl", "create", "secret", "generic", "-n", "metallb-system", "memberlist", "--from-literal=secretkey=\"{0}\"".format(randKey.decode("utf-8"))],
    ["kubectl", "apply", "-f", valuePath]
  ]

  try:
    with os.fdopen(fd, 'w+') as configTemp:
      configTemp.write(config.format(ip_range))

    for cmd in commands:
      try:
        if ctx.obj['DEBUG']:
          cmd = cmd + [ "--dry-run" ]
        logger.debug('executing: ' + ' '.join(cmd))
        subprocess.check_call(cmd)
      except subprocess.CalledProcessError as e:
        pass
  finally:
    os.remove(valuePath)


def check_ns_exists(ns):
  try:
    cmd = ["kubectl", "get", "namespace", ns ]
    subprocess.check_output(cmd)
    return True
  except subprocess.CalledProcessError as e:
    pass

  return False

def wipe_cluster(config):
  cluster_exists = False

  if "name" in config:
    try:
      cmd = ["kind", "get", "clusters"]
      result = subprocess.check_output(cmd, universal_newlines=True)
      for name in result.split('\n'):
        if config["name"] == name.strip():
          cluster_exists = True
    except:
      pass
  else:
    logger.critical("kind configuration requires cluster `name`: see readme")
    exit(1)

  if cluster_exists == False:
    logger.warning("cluster named `{0}` not found".format(config["name"]))

  cmd = ["kind", "delete", "cluster", "--name", config["name"]]

  try:
    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    logger.critical(e.output)
    exit(1)

def create_cluster(config):
  if "name" in config:
    try:
      cmd = ["kind", "get", "clusters"]
      logger.debug('executing: ' + ' '.join(cmd))
      result = subprocess.check_output(cmd, universal_newlines=True)
      for name in result.split('\n'):
        if config["name"] == name.strip():
          logger.info("cluster named `{0}` already exists -- Skipping cluster creation".format(config["name"]))
          return
    except:
      pass
  else:
    logger.critical("Kind configuration requires cluster `name`: see readme")
    exit(1)

  if "config.yaml" in config:
    # kind.config is required
    fd, valuePath = tempfile.mkstemp()
    try:
      with os.fdopen(fd, 'w+') as configTemp:
        configTemp.write(config["config.yaml"])

      try:
        cmd = ["kind", "create", "cluster", "--name", config["name"], "--config", valuePath]
        logger.debug('executing: ' + ' '.join(cmd))
        subprocess.check_call(cmd)
      except subprocess.CalledProcessError as e:
        logger.error(e.output)
        pass
    finally:
      os.remove(valuePath)

  else:
    logger.critical("Kind configuration is required: see readme")
    exit(1)


def delete_namespace(ctx, namespace):
  if check_ns_exists(namespace) == False:
    logger.info("{0} already removed, skipping".format(namespace))
    return

  cmd = ["kubectl", "delete", "namespace", namespace]
  try:
    if ctx.obj['DEBUG']:
      cmd = cmd + [ "--dry-run" ]
    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass

def create_namespace(ctx, namespace):
  if check_ns_exists(namespace):
    logger.info("{0} already exists, skipping".format(namespace))
    return

  cmd = ["kubectl", "create", "namespace", namespace]
  try:
    if ctx.obj['DEBUG']:
      cmd = cmd + [ "--dry-run" ]
    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    logger.error(e.output)
    pass

def install_maesh(ctx):
  cmd = [ "helm", "upgrade", "--install", "--reset-values", "maesh", "maesh/maesh" ]

  try:
    if ctx.obj['DEBUG']:
      cmd = cmd + [ "--dry-run" ]
    logger.debug('executing: ' + ' '.join(cmd))
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as e:
    pass


def install_traefik(ctx):
  if 'traefik' in ctx.obj['CMDR_PROJ'] and 'values' in ctx.obj['CMDR_PROJ']['traefik']:
    commands = [
      [
        "helm", "upgrade", "--install", "--reset-values",
        "--values", ctx.obj['CMDR_PROJ']['traefik']['values'],
        "traefik", ctx.obj['CMDR_PROJ']['traefik']['chart']
      ]
    ]

    for cmd in commands:
      try:
        if ctx.obj['DEBUG']:
          cmd = cmd + [ "--dry-run" ]
        logger.debug('executing: ' + ' '.join(cmd))
        subprocess.check_call(cmd)
      except subprocess.CalledProcessError as e:
        pass
  else:
    logger.error("ERROR: missing values configuration for traefik -- see readme")

cli.add_command(update_helm)
cli.add_command(reinstall_traefik)
cli.add_command(reload_traefik)
cli.add_command(deploy)
cli.add_command(setup)
cli.add_command(wipe)