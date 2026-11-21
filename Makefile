SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help init up down ps logs install executor test smoke lint web-install web governor build

help:
	@sed -n 's/^##//p' $(MAKEFILE_LIST)

## init           复制配置模板
init:
	bash scripts/dev.sh init

## up             启动基础设施
up:
	bash scripts/dev.sh up

## down           停止基础设施
down:
	bash scripts/dev.sh down

## ps             容器状态
ps:
	bash scripts/dev.sh ps

## logs           容器日志
logs:
	bash scripts/dev.sh logs

## install        安装执行层依赖
install:
	bash scripts/dev.sh install

## executor       启动 FastAPI
executor:
	bash scripts/dev.sh executor

## test           运行测试
test:
	bash scripts/dev.sh test

## smoke          mock LLM 冒烟测试
smoke:
	bash scripts/dev.sh smoke

## lint           ruff 检查
lint:
	bash scripts/dev.sh lint

## web-install    安装前端依赖
web-install:
	bash scripts/dev.sh web-install

## web            启动前端
web:
	bash scripts/dev.sh web

## governor       启动治理层
governor:
	bash scripts/dev.sh governor

## build          构建镜像
build:
	bash scripts/dev.sh build
