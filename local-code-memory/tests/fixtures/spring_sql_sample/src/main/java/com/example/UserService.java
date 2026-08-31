package com.example;

public class UserService {
    private final UserRepository repository;

    public UserService(UserRepository repository) {
        this.repository = repository;
    }

    public User createUser(String name) {
        return repository.save(new User(name));
    }
}
