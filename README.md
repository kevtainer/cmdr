## WARNING - THIS PROJECT IS STILL A WIP

Getting Started
---

This application requires poetry / python 3 to generate a working executable

* @todo - package releases / CI
* @todo - document `cmdr-project.yaml` format and use cases

### Building

* Clone this repo
* Run `poetry install && poetry shell`
* Run `pip install --editable cmdr/`
* `cmdr --help` will give you a list of options, but probably won't be that helpful (did i mention WIP?)

### Using

This is a highly opinionated tool to spin up stacks using values stored in `cmdr-project.yaml`, which is usually stored alongside a microservice application stack. It uses the following tools to deploy multiple services and back-ends (databases, message queues, etc) for local development and demos:

* Docker (19.03.8+)
* Kind (0.8.0+)
* kubectl (1.17.4+)
* Helm (3.0.1+)

This tool is used to stand up the microservice demo application  [spring-petclinic-kubernetes](https://github.com/notsureifkevin/spring-petclinic-kubernetes/tree/traefik2), and may be used for others in the future.